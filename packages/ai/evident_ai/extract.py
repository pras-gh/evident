"""Entity extraction from parsed filings.

This is the one place in the pipeline where a language model belongs. Turning
"During fiscal 2025 the Company increased payments for acquisition of property,
plant and equipment" into a `metric` entity is a reading task, not a parsing
task.

Two guardrails matter more than the extraction itself.

The model returns one of eight types or nothing — the set is an `enum` in the
schema, so an unknown type is a malformed response rather than a row nobody can
group. And every entity must cite a paragraph id we supplied; anything citing an
id we did not is **dropped, not stored**. A hallucinated citation is worse than
a missing entity, because it looks exactly like a real one until someone clicks
through.

Both rejections are counted. A rising drop rate is the signal that a prompt or
model change has started inventing things, and it is the only signal there is.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from evident_graph.taxonomy import (TYPE_NAMES, Extracted, InvalidEntity,
                                    validate)
from evident_parser.models import Block

from .prompts import EXTRACT_ENTITIES, MODEL

#: Non-streaming default from the SDK guidance. Extraction output is small, but
#: a truncated response is an unparseable one, so there is no reason to shave it.
MAX_TOKENS = 16000


# --------------------------------------------------------------------- schema
def json_schema() -> dict[str, Any]:
    """The structured-output schema.

    `entity_type` is an enum over the canonical types and `additionalProperties`
    is false, so the model cannot invent a type or smuggle an extra field past
    validation. Generated from `TYPE_NAMES` rather than written out — the same
    tuple that produces the prompt text and the CHECK constraint.
    """
    return {
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "as the filing names it, not expanded",
                        },
                        "entity_type": {"type": "string", "enum": list(TYPE_NAMES)},
                        "confidence": {"type": "number"},
                        "paragraph_id": {
                            "type": "string",
                            "description": "an id from the input, never invented",
                        },
                        "quote": {
                            "type": "string",
                            "description": "verbatim span supporting this entity",
                        },
                        "description": {
                            "type": "string",
                            "description": "one clause on what it is, if the text says",
                        },
                        "period": {
                            "type": "string",
                            "description": "metric only: the period the figure covers",
                        },
                        "value": {
                            "type": "number",
                            "description": "metric only: the figure, if one is stated",
                        },
                        "unit": {
                            "type": "string",
                            "description": "metric only: e.g. USD millions, percent",
                        },
                    },
                    "required": ["name", "entity_type", "confidence",
                                 "paragraph_id", "quote"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["entities"],
        "additionalProperties": False,
    }


# ------------------------------------------------------------------ guardrail
@dataclass(slots=True)
class DropReport:
    """What the extractor refused, and why.

    `reasons` is kept because "23 dropped" tells you something is wrong and
    nothing about what. Uncited entities and unknown types are different
    failures with different fixes.
    """
    kept: int = 0
    dropped: int = 0
    bad_ids: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def drop_rate(self) -> float:
        total = self.kept + self.dropped
        return self.dropped / total if total else 0.0


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


def validate_all(raw: Iterable[dict], valid_ids: set[str],
                 report: DropReport) -> list[Extracted]:
    """Validate every returned object, keeping the survivors."""
    kept: list[Extracted] = []
    for obj in raw:
        try:
            kept.append(validate(obj, valid_paragraph_ids=valid_ids))
        except InvalidEntity as exc:
            report.dropped += 1
            report.reasons.append(str(exc))
            pid = str((obj or {}).get("paragraph_id"))
            if pid not in valid_ids:
                report.bad_ids.append(pid)
        else:
            report.kept += 1
    return kept


# ------------------------------------------------------------------- request
def render(blocks: Sequence[Block]) -> str:
    return "\n\n".join(f"[{b.paragraph_id}] {b.text}" for b in blocks)


def _system() -> list[dict[str, Any]]:
    """The system prompt, marked cacheable.

    It is byte-identical for every chunk of every filing, so it is the ideal
    cache prefix. Caching needs a prefix of roughly 1024 tokens to engage —
    below that the block is sent normally and nothing breaks, it just does not
    save anything. `cache_hit_rate()` is how you find out which happened.
    """
    return [{"type": "text", "text": EXTRACT_ENTITIES.system,
             "cache_control": {"type": "ephemeral"}}]


def _request_params(blocks: Sequence[Block], *, max_tokens: int,
                    effort: str | None) -> dict[str, Any]:
    output_config: dict[str, Any] = {
        "format": {"type": "json_schema", "schema": json_schema()}
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


def parse_response(response: Any, blocks: Sequence[Block],
                   report: DropReport) -> list[Extracted]:
    """Pull entities out of one response and validate them.

    `output_config.format` guarantees a text block holding valid JSON, but the
    response may lead with thinking blocks, so the text block is selected by
    type rather than position.
    """
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:  # pragma: no cover - would mean a refusal or empty turn
        report.reasons.append(
            f"no text block in response (stop_reason={response.stop_reason})")
        return []
    payload = json.loads(text)
    valid = {b.paragraph_id for b in blocks}
    return validate_all(payload.get("entities") or [], valid, report)


# ----------------------------------------------------------------- extractors
def extract_from_blocks(
    blocks: Sequence[Block],
    *,
    client: Any | None = None,
    max_tokens: int = MAX_TOKENS,
    effort: str | None = None,
) -> tuple[list[Extracted], DropReport]:
    """Extract canonical entities from one group of paragraphs.

    Returns a flat list — each `Extracted` carries its own type and its own
    single citation, so an entity mentioned in four paragraphs comes back four
    times. That is deliberate: repetition is how importance gets measured, and
    collapsing it here would throw the signal away before it is counted.
    """
    report = DropReport()
    if not blocks:
        return [], report

    response = _client(client).messages.create(
        **_request_params(blocks, max_tokens=max_tokens, effort=effort))
    return parse_response(response, blocks, report), report


def extract_batched(
    groups: dict[str, Sequence[Block]],
    *,
    client: Any | None = None,
    max_tokens: int = MAX_TOKENS,
    effort: str | None = None,
) -> Any:
    """Submit many groups as one Batch API job, at half price.

    A 10-K is a few hundred chunks and nothing about backfilling a 2019 filing
    is latency-sensitive, so the synchronous path is the wrong default for bulk
    ingestion. Returns the batch object; poll it and pass the results to
    `collect_batch`.
    """
    from anthropic.types.message_create_params import \
        MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    requests = [
        Request(
            custom_id=key,
            params=MessageCreateParamsNonStreaming(
                **_request_params(blocks, max_tokens=max_tokens, effort=effort)),
        )
        for key, blocks in groups.items() if blocks
    ]
    return _client(client).messages.batches.create(requests=requests)


def collect_batch(results: Iterable[Any], groups: dict[str, Sequence[Block]],
                  ) -> tuple[dict[str, list[Extracted]], DropReport]:
    """Match batch results back to their groups.

    Results come back in arbitrary order, so they are keyed by `custom_id` and
    never by position — reading them positionally would silently attach one
    chunk's entities to another chunk's paragraph ids, which the citation
    guardrail would then dutifully drop as uncited.
    """
    report = DropReport()
    out: dict[str, list[Extracted]] = {}
    for result in results:
        key = result.custom_id
        blocks = groups.get(key)
        if blocks is None:
            report.reasons.append(f"batch returned unknown custom_id {key!r}")
            continue
        if result.result.type != "succeeded":
            report.reasons.append(f"{key}: batch request {result.result.type}")
            continue
        out[key] = parse_response(result.result.message, blocks, report)
    return out, report


def cache_hit_rate(usage: Any) -> float:
    """Share of input tokens served from cache, for one response.

    Zero across repeated calls means the cached prefix is being invalidated —
    or that the system prompt is under the ~1024-token minimum and never cached
    at all. Both are worth knowing before assuming the saving is real.
    """
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    fresh = getattr(usage, "input_tokens", 0) or 0
    created = getattr(usage, "cache_creation_input_tokens", 0) or 0
    total = read + fresh + created
    return read / total if total else 0.0
