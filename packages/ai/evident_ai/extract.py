"""Entity extraction from parsed filings — the whole path against Claude.

    chunk -> Claude API -> JSON Schema -> Pydantic -> entities + relationships

This is the one place in the pipeline where a language model belongs. Turning
"During fiscal 2025 the Company increased payments for acquisition of property,
plant and equipment" into a `metric` entity is a reading task, not a parsing
task.

**Nothing here is free-form.** The request constrains the API with a schema
generated from the Pydantic models; the response becomes objects only through
`EntityExtractionResponse`; and a response that will not parse is rejected
whole. There is no code path from raw model text to a database row.

Two rules cannot be written into a schema, because they depend on the input we
sent, so they are enforced after parsing:

- an entity must cite a `paragraph_id` we actually supplied
- a relationship's endpoints must both be entities in the same response

Those are per item. A hallucinated citation costs that entity, not the other
nineteen from the chunk. Every drop carries a reason, because "23 dropped" says
something is wrong and nothing about what — and a rising drop rate is the only
signal that a prompt or model change has started inventing things.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from evident_graph.taxonomy import slug as slugify
from evident_parser.models import Block
from pydantic import ValidationError

from .prompts import EXTRACT_ENTITIES, MODEL
from .schema import (EntityExtractionResponse, ExtractedEntity,
                     ExtractedRelationship, wire_schema)

#: Non-streaming default from the SDK guidance. A truncated response is an
#: unparseable one, so there is no reason to shave this.
MAX_TOKENS = 16000


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


# ------------------------------------------------------------- post-validation
def validate_entities(entities: Iterable[ExtractedEntity], valid_ids: set[str],
                      report: DropReport) -> list[ExtractedEntity]:
    kept: list[ExtractedEntity] = []
    for e in entities:
        if e.paragraph_id not in valid_ids:
            report.dropped += 1
            report.bad_ids.append(e.paragraph_id)
            report.reasons.append(
                f"{e.name!r}: cites paragraph {e.paragraph_id!r}, "
                "which was not supplied")
            continue
        report.kept += 1
        kept.append(e)
    return kept


def validate_relationships(relationships: Iterable[ExtractedRelationship],
                           entities: Sequence[ExtractedEntity],
                           valid_ids: set[str],
                           report: DropReport) -> list[ExtractedRelationship]:
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
        report.kept += 1
        kept.append(r)
    return kept


# ------------------------------------------------------------------- request
def render(blocks: Sequence[Block]) -> str:
    return "\n\n".join(f"[{b.paragraph_id}] {b.text}" for b in blocks)


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
                   effort: str | None = None) -> dict[str, Any]:
    output_config: dict[str, Any] = {
        "format": {"type": "json_schema", "schema": wire_schema()}
    }
    if effort:
        output_config["effort"] = effort
    return {
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": _system(),
        "messages": [{"role": "user", "content": render(blocks)}],
        "output_config": output_config,
    }


def _client(client: Any | None) -> Any:
    if client is not None:
        return client
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - env dependent
        raise RuntimeError(
            "Entity extraction needs the anthropic SDK — "
            "`pip install -r requirements.txt`"
        ) from exc
    if not (os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise RuntimeError(
            "No Anthropic credentials found. Run `ant auth login`, or set "
            "ANTHROPIC_API_KEY."
        )
    return anthropic.Anthropic()


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

    started = time.perf_counter()
    response = _client(client).messages.create(
        **request_params(blocks, max_tokens=max_tokens, effort=effort))
    latency_ms = int((time.perf_counter() - started) * 1000)

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
        if exc.raw is None:
            exc.raw = raw
        raise

    valid = {b.paragraph_id for b in blocks}
    report = DropReport()
    entities = validate_entities(parsed.entities, valid, report)
    relationships = validate_relationships(parsed.relationships, entities,
                                           valid, report)
    return Extraction(entities, relationships, report, usage, raw=raw,
                      stop_reason=stop, latency_ms=latency_ms,
                      entities_returned=len(parsed.entities),
                      relationships_returned=len(parsed.relationships))


def extract_document(
    groups: dict[str, Sequence[Block]],
    *,
    client: Any | None = None,
    max_tokens: int = MAX_TOKENS,
    effort: str | None = None,
    on_result: "Callable[[str, Extraction | ExtractionRejected], None] | None" = None,
) -> tuple[dict[str, Extraction], DropReport, Usage]:
    """Every chunk of a document, one call each, rejections survived.

    `on_result` is called once per chunk with either the `Extraction` or the
    `ExtractionRejected` — so a benchmark can record what came back, including
    the raw text of a response that failed, without reimplementing the loop or
    the persistence that follows it.

    The synchronous path. For backfilling a whole filing, `submit_batch` is
    half the price and nothing about a 2019 10-K is latency-sensitive.
    """
    out: dict[str, Extraction] = {}
    overall = DropReport()
    total = Usage()
    for key, blocks in groups.items():
        try:
            result = extract_from_blocks(blocks, client=client,
                                         max_tokens=max_tokens, effort=effort)
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
    """Submit every chunk as one Batch API job, at half price."""
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
    return _client(client).messages.batches.create(requests=requests)


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
        report = DropReport()
        entities = validate_entities(parsed.entities, valid, report)
        relationships = validate_relationships(parsed.relationships, entities,
                                               valid, report)
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
