"""Filing summaries — the first AI feature a customer calls directly.

    paragraphs -> gateway (task filing_summarization) -> JSON Schema -> Pydantic

Business logic in the sense the gateway means: it names a task and a prompt,
never a provider or a model. Like extraction, the answer is validated whole,
and a key point citing a paragraph that was not in the input is dropped.

Cached by filing accession and prompt version, shared across organizations:
the input is public filing text and our own prompt, so one customer's paid
summary can answer the next one's request for free.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .gateway import AIContext, CacheSpec, LLMRequest, LLMResponse, TaskType, gateway
from .prompts import Prompt
from .schema import schema_for

SUMMARIZE_FILING = Prompt(
    name="summarize_filing",
    version="1.0.0",
    system=(
        "You summarise sections of SEC filings for investment research.\n\n"
        "The user message is the filing's text as numbered paragraphs, each prefixed "
        "with its id in brackets, like [27_3].\n\n"
        "Write a neutral, factual summary of what the filing says — not what you know "
        "about the company from elsewhere. Then list the key points a research analyst "
        "would need, most material first. Every key point must cite the ids of the "
        "paragraphs that state it, exactly as given; never cite an id that was not in "
        "the input, and never state something no paragraph supports.\n\n"
        "Be concise: the summary in at most 150 words, at most 8 key points of at most "
        "40 words each. Use the filing's own terms for products, regulations and places."
    ),
)

MAX_OUTPUT_TOKENS = 2_000


class KeyPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    point: str = Field(min_length=1)
    paragraph_ids: list[str] = Field(min_length=1)


class FilingSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1)
    key_points: list[KeyPoint] = Field(default_factory=list)


class SummaryFailed(RuntimeError):
    """The model answered, but not with a usable summary."""

    def __init__(self, reason: str, response: LLMResponse):
        super().__init__(reason)
        self.reason = reason
        self.response = response


@dataclass(slots=True)
class SummaryResult:
    summary: str
    key_points: list[KeyPoint]
    response: LLMResponse
    dropped_citations: list[str] = field(default_factory=list)


def render(paragraphs: Sequence[tuple[str, str]]) -> str:
    return "\n\n".join(f"[{pid}] {text}" for pid, text in paragraphs)


def summarize_filing(*, accession: str, paragraphs: Sequence[tuple[str, str]],
                     ctx: AIContext, gw=None) -> SummaryResult:
    """Raises UsageLimitExceeded, AllProvidersFailed (from the gateway), or
    SummaryFailed when the answer cannot be used."""
    request = LLMRequest(
        task=TaskType.FILING_SUMMARIZATION, system=SUMMARIZE_FILING.system,
        user=render(paragraphs), max_output_tokens=MAX_OUTPUT_TOKENS,
        json_schema=schema_for(FilingSummary),
        cache=CacheSpec(accession=accession, prompt_version=SUMMARIZE_FILING.id, shared=True))
    response = (gw or gateway()).complete(request, ctx)
    if response.stop != "complete":
        raise SummaryFailed(f"the model stopped early ({response.stop})", response)
    try:
        parsed = FilingSummary.model_validate_json(response.text)
    except ValidationError as exc:
        raise SummaryFailed(f"the answer did not match the summary schema: "
                            f"{exc.errors()[0]['msg']}", response) from None

    valid = {pid for pid, _ in paragraphs}
    dropped: list[str] = []
    kept: list[KeyPoint] = []
    for kp in parsed.key_points:
        cited = [p for p in kp.paragraph_ids if p in valid]
        dropped += [p for p in kp.paragraph_ids if p not in valid]
        if cited:                       # a point with no real citation is not kept
            kept.append(KeyPoint(point=kp.point, paragraph_ids=cited))
    return SummaryResult(parsed.summary, kept, response, dropped)
