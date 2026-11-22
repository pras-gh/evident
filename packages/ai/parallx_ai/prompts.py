"""Claude prompts, versioned.

Prompts live in their own module and carry a version because a changed prompt
changes extractions. Storing the version alongside every extraction is what
makes a result reproducible — without it, "the numbers moved" is ambiguous
between the company restating and us rewording an instruction.
"""
from __future__ import annotations

from dataclasses import dataclass

from parallx_graph.taxonomy import ENTITY_TYPES, RELATIONSHIP_TYPES

#: Only what a request names when sent to an injected Anthropic client or the
#: Batch API. Requests through the gateway are routed by config/ai.toml, which
#: ignores this.
MODEL = "claude-sonnet-5-5"


@dataclass(frozen=True, slots=True)
class Prompt:
    name: str
    version: str
    system: str

    @property
    def id(self) -> str:
        return f"{self.name}@{self.version}"


def _rel_table() -> str:
    return "\n".join(
        f"- {r.name}: {r.definition} e.g. {r.examples[0]}." for r in RELATIONSHIP_TYPES)


def _type_table() -> str:
    """Render the taxonomy into prompt text.

    Generated rather than written out, so a type cannot exist in the schema and
    be missing from the instructions — the failure mode there is the model
    returning a valid enum value it was never told the meaning of.
    """
    lines = []
    for t in ENTITY_TYPES:
        examples = ", ".join(t.examples)
        lines.append(f"- {t.name}: {t.definition} Examples: {examples}.")
    return "\n".join(lines)


EXTRACT_ENTITIES = Prompt(
    name="extract_entities",
    version="4.2.0",
    system=f"""You extract structured company intelligence from SEC filings.

You will be given numbered paragraphs from one filing. Each paragraph has an id.
The request may also list topics already on file for this company.

Every entity you return must belong to exactly one of these eight types:

{_type_table()}

Rules:
- Use only these eight types. If something does not fit one of them, do not
  return it. There is no "other".
- Every entity MUST cite the paragraph_id it came from, and quote the exact
  span that supports it, copied verbatim from that paragraph. Never invent an
  id. Quotes are checked against the paragraph; one that is not found there
  is discarded with its entity.
- Name each entity as a short topic: one to five words naming the thing
  itself, never a sentence, clause or list about it. "Supplier
  Concentration", not "our reliance on a limited number of suppliers for key
  components"; "Labor Shortages", not "shortages of qualified labor, rising
  wages and union activity".
- Keep the filing's own words for proper names, and do not expand or
  embellish them: "Model X200", not "the Model X200 platform family".
- A name must make sense on its own: "Warranty Charge", not "Charge".
- A product and a charge, revenue figure or restriction tied to it are
  separate entities. A paragraph about a write-down of Model X200 inventory
  supports both "Model X200" (product) and "Model X200 Inventory Charge"
  (metric): return both.
- When the request lists topics already on file and a paragraph is about one
  of them, use that topic's name exactly as listed, even where this filing
  words it differently. Reuse a name only for the same thing: one product's
  revenue is not the company's total "Revenue", and a specific rule, product
  or event is not the broader topic it falls under. A new "Clean Air Act"
  rule is its own entity, not "Environmental Regulation", even when
  "Environmental Regulation" is listed. Return the specific one, and the
  broader one too when the paragraph is also about it. Name a new topic only
  when nothing on the list is the same thing. Consistent names are how a
  topic is followed from one year's filing to the next.
- Return an entity once per paragraph that supports it. Repeats across
  different paragraphs are wanted; they are how importance is measured.
- confidence is how strongly the quoted span supports the entity: 1.0 when the
  text states it outright, lower when it is implied. Do not inflate. A low
  score is more useful than a confident wrong one.
- The filer itself is never a `company` entity. Its own segments are
  `segment`, its own products are `product`.
- Do not infer, speculate, or fill gaps. Returning fewer entities is correct
  when the text does not support more.

Also return the relationships the text asserts between the entities you found.
Use exactly one of these types:

{_rel_table()}

Relationship rules:
- Both endpoints must be entities you returned in `entities`, named the same
  way. An edge to something you did not extract will be discarded.
- Only assert what the text states or clearly implies. Two things appearing in
  the same sentence is not a relationship — co-occurrence is computed
  separately and does not need your help.
- Cite the paragraph_id and quote the span, exactly as for entities.
- Returning no relationships is correct and common. Most paragraphs assert
  none.

Worked example. Given this input:

  Topics already on file for this company. ...
  - Export Controls (risk)
  - China (geography)

  Paragraphs:

  [7_1] NVIDIA continues investing in AI infrastructure to accelerate Blackwell
  deployment despite export restrictions into China.

a correct response is:

  entities:
    Blackwell           product     0.99  "accelerate Blackwell deployment"
    AI Infrastructure   strategy    0.97  "investing in AI infrastructure"
    China               geography   0.98  "into China"
    Export Controls     risk        0.95  "despite export restrictions"
  relationships:
    AI Infrastructure -> Blackwell   drives_investment  0.90

Note the name "Export Controls": the paragraph says "export restrictions", but
that topic is already on file, so its name is reused. And note what is *not*
returned: NVIDIA itself is the filer, so it is not a `company` entity;
"deployment" is not a product; and no relationship is asserted between China
and Blackwell, because the sentence places them near each other without
saying one affects the other.""",
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

EXTRACT_RELATIONSHIPS = Prompt(
    name="extract_relationships",
    version="1.0.0",
    system="""You identify relationships between entities a filing discusses.

You are given a list of entities already extracted from one filing, and the
paragraphs they came from.

Rules:
- Both endpoints must be from the supplied entity list. Never introduce an
  entity that is not in it.
- Use a short verb phrase for the relationship: drives_investment, constrains,
  competes_with, supplies, depends_on, part_of, replaces.
- Only assert a relationship the text states or clearly implies. Two things
  being mentioned nearby is not a relationship — co-occurrence is already
  computed separately and does not need your help.
- Cite the paragraph_id that supports each relationship.
- Returning nothing is correct when the text asserts nothing.""",
)

ALL = (EXTRACT_ENTITIES, EXTRACT_RELATIONSHIPS, RESOLVE_PROMISE,
       SUMMARISE_REVISION)
