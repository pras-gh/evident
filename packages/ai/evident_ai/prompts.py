"""Claude prompts, versioned.

Prompts live in their own module and carry a version because a changed prompt
changes extractions. Storing the version alongside every extraction is what
makes a result reproducible — without it, "the numbers moved" is ambiguous
between the company restating and us rewording an instruction.
"""
from __future__ import annotations

from dataclasses import dataclass

MODEL = "claude-opus-5"


@dataclass(frozen=True, slots=True)
class Prompt:
    name: str
    version: str
    system: str

    @property
    def id(self) -> str:
        return f"{self.name}@{self.version}"


EXTRACT_ENTITIES = Prompt(
    name="extract_entities",
    version="1.0.0",
    system="""You extract structured company intelligence from SEC filings.

You will be given numbered paragraphs from one section of a filing. Each has an
id. Extract only what the text actually supports.

Rules:
- Every entity you return MUST cite the paragraph_id it came from. Never invent
  an id; use only ids from the input.
- Quote the exact span that supports each entity, copied verbatim.
- A promise is a forward-looking commitment with a horizon ("we expect to ship
  in H2", "we plan to double capacity next year"). Boilerplate safe-harbour
  language is not a promise.
- A metric is a named, quantified measure. Record the period it refers to.
- Do not infer, speculate, or fill gaps. Returning fewer entities is correct
  when the text does not support more.""",
)

RESOLVE_PROMISE = Prompt(
    name="resolve_promise",
    version="1.0.0",
    system="""You decide whether a later filing settles an earlier commitment.

You are given one open promise and passages from filings published after it.

Rules:
- Answer `kept` or `broken` only when a passage states the outcome. Quote it.
- Answer `unclear` when the passages do not address the commitment. Silence is
  not evidence of failure, and guessing here would put a false claim in front of
  someone making a decision.
- Answer `abandoned` only when the company explicitly withdraws the commitment.
- Never infer an outcome from the company having stopped talking about it.""",
)

SUMMARISE_REVISION = Prompt(
    name="summarise_revision",
    version="1.0.0",
    system="""You write one sentence describing what changed on a memory card.

You are given the previous and current facts. Describe only the difference.
State direction and magnitude for numbers. Name what was added or removed.
If nothing changed, say exactly: Restated without change.""",
)

ALL = (EXTRACT_ENTITIES, RESOLVE_PROMISE, SUMMARISE_REVISION)
