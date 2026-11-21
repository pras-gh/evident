"""Entity extraction from parsed filings.

    chunk -> AI gateway -> JSON Schema -> Pydantic -> entities + relationships

Requests go through the AI gateway (task `financial_extraction`), which picks
the provider and model from `config/ai.toml`, and limits, logs and costs every
call. Tests and the demo inject their own Messages-API-shaped client instead.

This is the one place in the pipeline where a language model belongs. Turning
"During fiscal 2025 the Company increased payments for acquisition of property,
plant and equipment" into a `metric` entity is a reading task, not a parsing
task.

**Nothing here is free-form.** The request constrains the API with a schema
generated from the Pydantic models; the response becomes objects only through
`EntityExtractionResponse`; and a response that will not parse is rejected
whole. There is no code path from raw model text to a database row.

Some rules cannot be written into a schema, because they depend on the input we
sent, so they are enforced after parsing:

- an entity must cite a `paragraph_id` we actually supplied
- its quote must be found in that paragraph — then it is widened to the whole
  sentence, so the evidence reads as a statement rather than a bare name
- its name must be a topic, not a phrase about one (`MAX_NAME_WORDS`)
- a relationship's endpoints must both be entities in the same response

Those are per item. A hallucinated citation costs that entity, not the other
nineteen from the chunk. Every drop carries a reason, because "23 dropped" says
something is wrong and nothing about what — and a rising drop rate is the only
signal that a prompt or model change has started inventing things.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping, Sequence

from evident_graph.taxonomy import slug as slugify
from evident_parser import sentences
from evident_parser.models import Block
from pydantic import ValidationError

from .prompts import EXTRACT_ENTITIES, MODEL
from .schema import (EntityExtractionResponse, ExtractedEntity,
                     ExtractedRelationship, wire_schema)

#: Non-streaming default from the SDK guidance. A truncated response is an
#: unparseable one, so there is no reason to shave this.
MAX_TOKENS = 16000

#: A name longer than this, in words separated by spaces, is a phrase about a
#: topic rather than its name: "loss of data or other information technology
#: assets" (8). The prompt asks for one to five; this is the backstop, with room
#: for proper names such as "U.S. Department of Commerce's U.S. Entity List" (7).
MAX_NAME_WORDS = 7

#: A quote is widened to its sentence unless the sentence runs past this. A
#: 300-word list with no full stop is not evidence anyone will read.
MAX_QUOTE_CHARS = 600

#: Known topics listed in one request, at most (see `relevant_known`).
KNOWN_PER_REQUEST = 40

_WORDS = re.compile(r"\w+")
_ELLIPSIS = re.compile(r"\s*(?:\.{3}|\u2026)\s*")
_FOLD = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
                       "\u2013": "-", "\u2014": "-", "\u00a0": " "})


class ExtractionRejected(RuntimeError):
    """The response was not usable and produced nothing.

    Raised rather than returning a partial result. Salvaging the entities that
    happened to parse before a cut-off means storing data from a response we
    know was incomplete, which looks fine until someone audits it.
    """

    def __init__(self, reason: str, *, stop_reason: str | None = None,
                 raw: str | None = None, usage: "Usage | None" = None,
                 latency_ms: int | None = None, stop: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.stop_reason = stop_reason
        #: which model answered and what it cost, when the gateway answered
        self.model: str | None = None
        self.cost_usd: Decimal | None = None
        # kept for diagnosis, never parsed further
        self.raw = raw
        # A rejected response was still generated and still billed. Dropping
        # its tokens from the accounting would understate the cost of exactly
        # the runs you most want to notice.
        self.usage = usage
        self.latency_ms = latency_ms


@dataclass(slots=True)
class DropReport:
    """What was refused, and why."""
    kept: int = 0
    dropped: int = 0
    bad_ids: list[str] = field(default_factory=list)
    #: why individual items were dropped
    reasons: list[str] = field(default_factory=list)
    #: whole responses rejected — distinct from individual items dropped, and
    #: kept in its own list because the two have different causes and different
    #: fixes. A log that calls a dropped citation a "rejected response" sends
    #: someone looking for a truncation that never happened.
    rejected: int = 0
    rejections: list[str] = field(default_factory=list)

    @property
    def drop_rate(self) -> float:
        total = self.kept + self.dropped
        return self.dropped / total if total else 0.0


@dataclass(slots=True)
class Usage:
    """Token accounting, summed across requests.

    `cache_read` is the number that answers "is prompt caching actually
    working". It is reported rather than assumed, because the cached prefix
    only engages above roughly 1024 tokens and a prompt that falls just under
    caches nothing while looking exactly like one that does.
    """
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_created: int = 0

    def add(self, usage: Any) -> None:
        self.requests += 1
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_read += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_created += getattr(usage, "cache_creation_input_tokens", 0) or 0

    @property
    def hit_rate(self) -> float:
        total = self.cache_read + self.input_tokens + self.cache_created
        return self.cache_read / total if total else 0.0


@dataclass(slots=True)
class Extraction:
    """One chunk's worth of validated output."""
    entities: list[ExtractedEntity] = field(default_factory=list)
    relationships: list[ExtractedRelationship] = field(default_factory=list)
    report: DropReport = field(default_factory=DropReport)
    usage: Usage = field(default_factory=Usage)
    #: exactly what came back, kept so a schema change can be replayed offline
    raw: str | None = None
    stop_reason: str | None = None
    latency_ms: int | None = None
    #: returned vs kept is the acceptance rate for this chunk
    entities_returned: int = 0
    relationships_returned: int = 0
    #: "provider:model" that answered, and its cost, when the gateway answered
    model: str | None = None
    cost_usd: Decimal | None = None


def drop_uncited(items: Sequence[Any], valid_ids: set[str],
                 report: DropReport) -> list[Any]:
    """Discard anything citing a paragraph we did not supply."""
    kept = []
    for item in items:
        pid = getattr(item, "paragraph_id", None)
        if pid in valid_ids:
            kept.append(item)
            report.kept += 1
        else:
            report.dropped += 1
            report.bad_ids.append(str(pid))
    return kept


# ------------------------------------------------------------- quotes
def _folded(text: str) -> tuple[str, list[int]]:
    """`text` lower-cased, typographic quotes and dashes made plain, and
    whitespace runs collapsed — with each character's index in `text`."""
    out: list[str] = []
    index: list[int] = []
    space = False
    for i, ch in enumerate(text.translate(_FOLD)):
        if ch.isspace():
            if space:
                continue
            ch, space = " ", True
        else:
            space = False
        out.append(ch.lower())
        index.append(i)
    return "".join(out), index


def _locate_one(quote: str, paragraph: str, start: int = 0) -> tuple[int, int] | None:
    at = paragraph.find(quote, start)
    if at >= 0:
        return at, at + len(quote)
    q, _ = _folded(quote)
    t, index = _folded(paragraph)
    from_ = next((i for i, orig in enumerate(index) if orig >= start), len(t))
    at = t.find(q, from_) if q else -1
    if at < 0:
        return None
    return index[at], index[at + len(q) - 1] + 1


def locate(quote: str, paragraph: str) -> tuple[int, int] | None:
    """Where `quote` is in `paragraph`, allowing for case, curly quotes,
    dashes and whitespace — the differences a model introduces copying text
    — or None if it is not there.

    A quote that skips words with "..." is found when each fragment is, in
    order: "exports of certain chips... to China... and Russia" spans from
    the first fragment to the last."""
    quote = quote.strip()
    if not quote:
        return None
    whole = _locate_one(quote, paragraph)
    if whole is not None:
        return whole
    fragments = [f for f in _ELLIPSIS.split(quote) if f.strip()]
    if len(fragments) < 2:
        return None
    spans: list[tuple[int, int]] = []
    for fragment in fragments:
        span = _locate_one(fragment.strip(), paragraph, spans[-1][1] if spans else 0)
        if span is None:
            return None
        spans.append(span)
    return spans[0][0], spans[-1][1]


def anchor(quote: str, paragraph: str) -> str | None:
    """The sentence of `paragraph` that contains `quote`, as the paragraph
    has it; the span itself if that sentence is over MAX_QUOTE_CHARS; None if
    the quote is not in the paragraph at all."""
    span = locate(quote, paragraph)
    if span is None:
        return None
    start, end = sentences.around(paragraph, *span)
    if end - start > MAX_QUOTE_CHARS:
        start, end = span
    return paragraph[start:end]


def find(quote: str, paragraph_id: str,
         texts: Mapping[str, str]) -> tuple[str, str] | None:
    """(paragraph id, quote as its sentence) for a quote in the paragraph it
    cites — or in another part of the same paragraph. The chunker splits a
    long paragraph into parts (`28_3#1`, `28_3#2`), and a model citing one
    part for a sentence in the other has still cited the right paragraph; the
    citation moves to the part that has it, so the viewer highlights the
    right text. A quote found only in a different paragraph is not repaired:
    that is a different citation."""
    quote_ = anchor(quote, texts.get(paragraph_id, ""))
    if quote_ is not None:
        return paragraph_id, quote_
    base = paragraph_id.split("#", 1)[0]
    for other in sorted(texts):
        if other != paragraph_id and other.split("#", 1)[0] == base:
            quote_ = anchor(quote, texts[other])
            if quote_ is not None:
                return other, quote_
    return None


# ------------------------------------------------------------- post-validation
def _drop(report: DropReport, reason: str, bad_id: str | None = None) -> None:
    report.dropped += 1
    report.reasons.append(reason)
    if bad_id is not None:
        report.bad_ids.append(bad_id)


def validate_entities(entities: Iterable[ExtractedEntity], valid_ids: set[str],
                      report: DropReport, *,
                      texts: Mapping[str, str] | None = None) -> list[ExtractedEntity]:
    """Entities that cite a paragraph we sent and have a topic's name — and,
    given the paragraphs' `texts`, whose quote is in the paragraph cited, with
    the quote widened to its sentence."""
    kept: list[ExtractedEntity] = []
    for e in entities:
        if e.paragraph_id not in valid_ids:
            _drop(report, f"{e.name!r}: cites paragraph {e.paragraph_id!r}, "
                          "which was not supplied", e.paragraph_id)
            continue
        if len(e.name.split()) > MAX_NAME_WORDS:
            _drop(report, f"{e.name!r}: a phrase, not a topic's name "
                          f"(over {MAX_NAME_WORDS} words)")
            continue
        if texts is not None:
            found = find(e.quote, e.paragraph_id, texts)
            if found is None:
                _drop(report, f"{e.name!r}: quote is not in paragraph {e.paragraph_id}")
                continue
            e = e.model_copy(update={"paragraph_id": found[0], "quote": found[1]})
        report.kept += 1
        kept.append(e)
    return kept


def validate_relationships(relationships: Iterable[ExtractedRelationship],
                           entities: Sequence[ExtractedEntity],
                           valid_ids: set[str],
                           report: DropReport, *,
                           texts: Mapping[str, str] | None = None,
                           ) -> list[ExtractedRelationship]:
    """Keep only edges whose endpoints survived, matched on slug.

    Endpoints are matched by slug rather than by exact string because the model
    may name an entity `Blackwell` in the entity list and `the Blackwell
    platform` in an edge; both fold to `blackwell`. An edge to something that
    is not an entity is not an edge — it is a claim with one end unattached.
    """
    known = {slugify(e.name) for e in entities}
    kept: list[ExtractedRelationship] = []
    for r in relationships:
        src, dst = slugify(r.source_name), slugify(r.target_name)
        if r.paragraph_id not in valid_ids:
            report.dropped += 1
            report.bad_ids.append(r.paragraph_id)
            report.reasons.append(
                f"{r.source_name}->{r.target_name}: cites paragraph "
                f"{r.paragraph_id!r}, which was not supplied")
            continue
        if src == dst:
            report.dropped += 1
            report.reasons.append(f"{r.source_name}->{r.target_name}: self-edge")
            continue
        missing = [n for n, s in ((r.source_name, src), (r.target_name, dst))
                   if s not in known]
        if missing:
            report.dropped += 1
            report.reasons.append(
                f"{r.source_name}->{r.target_name}: endpoint(s) "
                f"{', '.join(repr(m) for m in missing)} are not entities in "
                "this response")
            continue
        if texts is not None:
            found = find(r.quote, r.paragraph_id, texts)
            if found is None:
                _drop(report, f"{r.source_name}->{r.target_name}: quote is not in "
                              f"paragraph {r.paragraph_id}")
                continue
            r = r.model_copy(update={"paragraph_id": found[0], "quote": found[1]})
        report.kept += 1
        kept.append(r)
    return kept


# ------------------------------------------------------------------- request
KNOWN_HEADER = ("Topics already on file for this company. When a paragraph is about "
                "one of these, use its name exactly as written here, even where this "
                "filing words it differently:")

_COMMON = frozenset({"the", "and", "for", "our", "its", "with", "from", "other",
                     "risk", "risks", "products", "product", "data"})


def relevant_known(known: Sequence[tuple[str, str]],
                   blocks: Sequence[Block]) -> list[tuple[str, str]]:
    """The known topics worth listing for these paragraphs: those sharing a
    word with them, in the order given (most-cited first), at most
    KNOWN_PER_REQUEST. "Export Controls" is listed for a paragraph about
    "export restrictions"; a topic with no word in common is unlikely to be
    the same thing, and listing every topic on file would cost tokens on
    every request."""
    present = set(_WORDS.findall(" ".join(b.text for b in blocks).lower()))
    out: list[tuple[str, str]] = []
    for name, kind in known:
        words = set(_WORDS.findall(name.lower())) - _COMMON
        # short words match too much, unless a name has nothing else ("AI")
        telling = {w for w in words if len(w) > 2} or words
        if telling & present:
            out.append((name, kind))
            if len(out) == KNOWN_PER_REQUEST:
                break
    return out


def render(blocks: Sequence[Block], known: Sequence[tuple[str, str]] = ()) -> str:
    paragraphs = "\n\n".join(f"[{b.paragraph_id}] {b.text}" for b in blocks)
    if not known:
        return paragraphs
    listing = "\n".join(f"- {name} ({kind})" for name, kind in known)
    return f"{KNOWN_HEADER}\n{listing}\n\nParagraphs:\n\n{paragraphs}"


def _system() -> list[dict[str, Any]]:
    """The system prompt, marked cacheable.

    Byte-identical for every chunk of every filing, so it is the ideal cache
    prefix. Caching engages above roughly 1024 tokens; below that the block is
    sent normally and nothing breaks, it just saves nothing. `cache_hit_rate()`
    is how you find out which happened rather than assuming.
    """
    return [{"type": "text", "text": EXTRACT_ENTITIES.system,
             "cache_control": {"type": "ephemeral"}}]


def request_params(blocks: Sequence[Block], *, max_tokens: int = MAX_TOKENS,
                   effort: str | None = None,
                   known: Sequence[tuple[str, str]] = ()) -> dict[str, Any]:
    """The request for one group of paragraphs. `known` is every topic on file
    for the company as (name, type), most-cited first; the ones relevant to
    these paragraphs are listed in the request."""
    output_config: dict[str, Any] = {
        "format": {"type": "json_schema", "schema": wire_schema()}
    }
    if effort:
        output_config["effort"] = effort
    return {
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": _system(),
        "messages": [{"role": "user",
                      "content": render(blocks, relevant_known(known, blocks))}],
        "output_config": output_config,
    }


def _client(client: Any | None, context: Any | None = None, cache: Any | None = None) -> Any:
    """The injected client, or the gateway behind a Messages-shaped facade.
    Extraction never builds a provider client of its own. `cache` (a gateway
    `CacheSpec`) applies only to the gateway; an injected client is used as is."""
    if client is not None:
        return client
    from .gateway import TaskType, system_context
    from .gateway.compat import GatewayMessages
    return GatewayMessages(TaskType.FINANCIAL_EXTRACTION,
                           context or system_context("extraction"), cache=cache)


# -------------------------------------------------------------------- parsing
def parse_response(response: Any) -> EntityExtractionResponse:
    """Response -> validated models, or `ExtractionRejected`.

    Every rejection below is a case where the response cannot be trusted as a
    whole, so nothing from it is kept.
    """
    stop = getattr(response, "stop_reason", None)

    if stop == "max_tokens":
        raise ExtractionRejected(
            "response hit max_tokens and the JSON is truncated", stop_reason=stop)

    if stop == "refusal":
        details = getattr(response, "stop_details", None)
        category = getattr(details, "category", None)
        raise ExtractionRejected(
            f"model refused the request (category={category})", stop_reason=stop)

    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise ExtractionRejected(
            "no text block in response", stop_reason=stop)

    try:
        return EntityExtractionResponse.model_validate_json(text)
    except ValidationError as exc:
        # Covers malformed JSON and schema-valid-looking JSON that still
        # violates the model — an unknown entity_type, a confidence of 1.4, an
        # extra field. Either way the response is refused whole.
        raise ExtractionRejected(
            f"response did not validate against EntityExtractionResponse: "
            f"{exc.error_count()} error(s); first: {exc.errors()[0]['msg']}",
            stop_reason=stop, raw=text) from exc


# ----------------------------------------------------------------- extractors
def extract_from_blocks(
    blocks: Sequence[Block],
    *,
    client: Any | None = None,
    max_tokens: int = MAX_TOKENS,
    effort: str | None = None,
    context: Any | None = None,
    cache: Any | None = None,
    known: Sequence[tuple[str, str]] = (),
) -> Extraction:
    """Extract entities and relationships from one group of paragraphs.

    Raises `ExtractionRejected` when the response is unusable. Callers working
    through a whole document catch it per chunk and carry on — one bad response
    should not cost the other 274.

    An entity mentioned in four paragraphs comes back four times. That is
    deliberate: repetition is how importance gets measured, and collapsing it
    here would throw the signal away before it is counted.
    """
    if not blocks:
        return Extraction()

    from .gateway import AllProvidersFailed

    started = time.perf_counter()
    try:
        response = _client(client, context, cache).messages.create(
            **request_params(blocks, max_tokens=max_tokens, effort=effort, known=known))
    except AllProvidersFailed as exc:
        # This chunk got no answer; the next may. A usage limit is not caught
        # here — it stops the document, because it stops all paid inference.
        raise ExtractionRejected(f"no provider answered: {exc}") from exc
    latency_ms = int((time.perf_counter() - started) * 1000)
    model = (f"{response.provider}:{response.model}"
             if getattr(response, "provider", None) else None)
    cost = getattr(response, "cost_usd", None)

    # Counted before parsing, so a rejected response still reports what it cost.
    usage = Usage()
    if getattr(response, "usage", None) is not None:
        usage.add(response.usage)

    stop = getattr(response, "stop_reason", None)
    raw = next((b.text for b in response.content if b.type == "text"), None)

    try:
        parsed = parse_response(response)
    except ExtractionRejected as exc:
        exc.usage = usage
        exc.latency_ms = latency_ms
        exc.model, exc.cost_usd = model, cost
        if exc.raw is None:
            exc.raw = raw
        raise

    valid = {b.paragraph_id for b in blocks}
    texts = {b.paragraph_id: b.text for b in blocks}
    report = DropReport()
    entities = validate_entities(parsed.entities, valid, report, texts=texts)
    relationships = validate_relationships(parsed.relationships, entities,
                                           valid, report, texts=texts)
    return Extraction(entities, relationships, report, usage, raw=raw,
                      stop_reason=stop, latency_ms=latency_ms,
                      entities_returned=len(parsed.entities),
                      relationships_returned=len(parsed.relationships),
                      model=model, cost_usd=cost)


def extract_document(
    groups: dict[str, Sequence[Block]],
    *,
    client: Any | None = None,
    max_tokens: int = MAX_TOKENS,
    effort: str | None = None,
    on_result: "Callable[[str, Extraction | ExtractionRejected], None] | None" = None,
    context: Any | None = None,
    cache: Any | None = None,
    known: Sequence[tuple[str, str]] = (),
) -> tuple[dict[str, Extraction], DropReport, Usage]:
    """Every chunk of a document, one call each, rejections survived.

    `on_result` is called once per chunk with either the `Extraction` or the
    `ExtractionRejected` — so a benchmark can record what came back, including
    the raw text of a response that failed, without reimplementing the loop or
    the persistence that follows it.

    `cache` — `CacheSpec(accession, prompt id, shared=True)` — lets the gateway
    answer a chunk it has extracted before for free: re-running a filing after
    a change to resolution or storage does not pay for the extraction again.
    A changed prompt, model or chunk is a miss.

    The synchronous path. For backfilling a whole filing, `submit_batch` is
    half the price and nothing about a 2019 10-K is latency-sensitive.
    """
    out: dict[str, Extraction] = {}
    overall = DropReport()
    total = Usage()
    for key, blocks in groups.items():
        try:
            result = extract_from_blocks(blocks, client=client,
                                         max_tokens=max_tokens, effort=effort,
                                         context=context, cache=cache, known=known)
        except ExtractionRejected as exc:
            if on_result is not None:
                on_result(key, exc)
            overall.rejected += 1
            overall.rejections.append(f"{key}: {exc.reason}")
            if exc.usage is not None:
                total.requests += exc.usage.requests
                total.input_tokens += exc.usage.input_tokens
                total.output_tokens += exc.usage.output_tokens
                total.cache_read += exc.usage.cache_read
                total.cache_created += exc.usage.cache_created
            continue
        if on_result is not None:
            on_result(key, result)
        out[key] = result
        overall.kept += result.report.kept
        overall.dropped += result.report.dropped
        overall.bad_ids += result.report.bad_ids
        overall.reasons += [f"{key}: {r}" for r in result.report.reasons]
        total.requests += result.usage.requests
        total.input_tokens += result.usage.input_tokens
        total.output_tokens += result.usage.output_tokens
        total.cache_read += result.usage.cache_read
        total.cache_created += result.usage.cache_created
    return out, overall, total


def submit_batch(groups: dict[str, Sequence[Block]], *,
                 client: Any | None = None, max_tokens: int = MAX_TOKENS,
                 effort: str | None = None) -> Any:
    """Submit every chunk as one Anthropic Batch API job, at half price.

    Needs an explicit Anthropic client. Batch jobs do not go through the
    gateway, so they are neither routed nor logged in `ai_usage_logs`; record
    their cost when collecting results. (Unused by the pipeline today.)
    """
    if client is None:
        raise RuntimeError("submit_batch needs an explicit Anthropic client; batch "
                           "jobs are not routed or logged by the AI gateway")
    from anthropic.types.message_create_params import \
        MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    requests = [
        Request(
            custom_id=key,
            params=MessageCreateParamsNonStreaming(
                **request_params(blocks, max_tokens=max_tokens, effort=effort)),
        )
        for key, blocks in groups.items() if blocks
    ]
    return client.messages.batches.create(requests=requests)


def collect_batch(results: Iterable[Any], groups: dict[str, Sequence[Block]],
                  *,
                  on_result: "Callable[[str, Extraction | ExtractionRejected], None] | None" = None,
                  ) -> tuple[dict[str, Extraction], DropReport]:
    """Match batch results back to their groups.

    Keyed by `custom_id`, never by position: results come back in arbitrary
    order, and reading them positionally would attach one chunk's entities to
    another chunk's paragraph ids — which the citation guard would then
    dutifully drop as uncited, turning an ordering bug into a quality mystery.
    """
    overall = DropReport()
    out: dict[str, Extraction] = {}
    for result in results:
        key = result.custom_id
        blocks = groups.get(key)
        if blocks is None:
            overall.rejected += 1
            overall.rejections.append(f"batch returned unknown custom_id {key!r}")
            continue
        if result.result.type != "succeeded":
            overall.rejected += 1
            overall.rejections.append(f"{key}: batch request {result.result.type}")
            continue
        message = getattr(result.result, "message", None)
        if message is None:
            # a succeeded result should always carry one; if it does not, that
            # is a rejection rather than an exception that kills the batch
            overall.rejected += 1
            overall.rejections.append(f"{key}: succeeded result carried no message")
            continue
        try:
            parsed = parse_response(message)
        except ExtractionRejected as exc:
            if on_result is not None:
                on_result(key, exc)
            overall.rejected += 1
            overall.rejections.append(f"{key}: {exc.reason}")
            if exc.usage is not None:
                total.requests += exc.usage.requests
                total.input_tokens += exc.usage.input_tokens
                total.output_tokens += exc.usage.output_tokens
                total.cache_read += exc.usage.cache_read
                total.cache_created += exc.usage.cache_created
            continue

        valid = {b.paragraph_id for b in blocks}
        texts = {b.paragraph_id: b.text for b in blocks}
        report = DropReport()
        entities = validate_entities(parsed.entities, valid, report, texts=texts)
        relationships = validate_relationships(parsed.relationships, entities,
                                               valid, report, texts=texts)
        result = Extraction(entities, relationships, report)
        if on_result is not None:
            on_result(key, result)
        out[key] = result
        overall.kept += report.kept
        overall.dropped += report.dropped
        overall.bad_ids += report.bad_ids
        overall.reasons += [f"{key}: {r}" for r in report.reasons]
    return out, overall


def cache_hit_rate(usage: Any) -> float:
    """Share of input tokens served from cache, for one response.

    Zero across repeated calls means the cached prefix is being invalidated, or
    that the system prompt is under the ~1024-token minimum and never cached at
    all. Both are worth knowing before assuming the saving is real.
    """
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    fresh = getattr(usage, "input_tokens", 0) or 0
    created = getattr(usage, "cache_creation_input_tokens", 0) or 0
    total = read + fresh + created
    return read / total if total else 0.0
