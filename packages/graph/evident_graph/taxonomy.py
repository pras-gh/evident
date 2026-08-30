"""The canonical entity types.

Eight types, closed set. The closure is the whole point: an open `type` field
produces `strategy`, `Strategy`, `strategic initiative` and `theme` for the same
idea inside a single filing, and nothing downstream can group them again.

The set is defined once, here, and consumed three ways — as the `enum` in the
extraction schema so the model cannot return anything else, as the text of the
prompt so it knows what the types mean, and as the `CHECK` constraint so no
other writer can bypass it. Adding a type means editing this file and writing a
migration, which is the correct amount of friction.
"""
from __future__ import annotations

from dataclasses import dataclass

from .normalize import canonical_label, entity_key


@dataclass(frozen=True, slots=True)
class EntityType:
    name: str
    definition: str
    examples: tuple[str, ...]


#: Order is fixed. It is rendered into the prompt, and a stable prompt is a
#: cacheable prompt — reordering this tuple silently invalidates the cached
#: prefix for every chunk in the corpus.
ENTITY_TYPES: tuple[EntityType, ...] = (
    EntityType(
        "strategy",
        "A direction the company is deliberately pursuing. The thing it is "
        "trying to do, not the thing it sells.",
        ("AI Infrastructure", "Cost Optimization", "Vertical Integration"),
    ),
    EntityType(
        "product",
        "A named product, platform, chip or service the company ships.",
        ("Blackwell", "CUDA", "Copilot"),
    ),
    EntityType(
        "executive",
        "A named individual officer or director. People only — never a team, "
        "a committee or a job title with no name attached.",
        ("Jensen Huang", "Tim Cook"),
    ),
    EntityType(
        "risk",
        "Something disclosed as able to harm the business.",
        ("Export Controls", "Supply Chain", "Customer Concentration"),
    ),
    EntityType(
        "metric",
        "A financial or operating measure the company reports.",
        ("Revenue", "Gross Margin", "CapEx"),
    ),
    EntityType(
        "segment",
        "A reporting segment or line of business.",
        ("Gaming", "Data Center", "Automotive"),
    ),
    EntityType(
        "company",
        "Another organisation — a customer, supplier, partner or competitor. "
        "Never the filer itself.",
        ("Microsoft", "AMD", "Amazon"),
    ),
    EntityType(
        "geography",
        "A country, region or market.",
        ("China", "Europe", "United States"),
    ),
)

TYPE_NAMES: tuple[str, ...] = tuple(t.name for t in ENTITY_TYPES)
_BY_NAME = {t.name: t for t in ENTITY_TYPES}


def is_valid_type(name: str) -> bool:
    return name in _BY_NAME


def describe(name: str) -> EntityType:
    return _BY_NAME[name]


def check_constraint(column: str = "entity_type") -> str:
    """The SQL `CHECK` body, generated from the same tuple as the prompt.

    Hand-writing this in the migration is how a type gets added to the model
    and rejected by the database six weeks later.
    """
    listed = ",".join(f"'{name}'" for name in TYPE_NAMES)
    return f"{column} in ({listed})"


def slug(name: str) -> str:
    """The stable per-company handle for an entity.

    This is `entity_key` under the name the schema uses. It keeps underscores
    rather than the hyphens a slug normally carries, because the graph API
    contract's node ids *are* these strings and clients cache them — switching
    separators would break every held id for a cosmetic gain.
    """
    return entity_key(name)


@dataclass(frozen=True, slots=True)
class Extracted:
    """One entity as the model returned it, after validation."""
    name: str
    entity_type: str
    confidence: float
    paragraph_id: str
    quote: str
    description: str | None = None
    # Only meaningful when entity_type == "metric". A metric entity is the
    # *name* of a measure; these carry the reading of it, which is a time
    # series rather than a mention and lands in metric_observations. They stay
    # optional because most metric mentions are prose that names no figure.
    period: str | None = None
    value: float | None = None
    unit: str | None = None

    @property
    def slug(self) -> str:
        return slug(self.name)


class InvalidEntity(ValueError):
    pass


def validate(raw: dict, *, valid_paragraph_ids: set[str]) -> Extracted:
    """Turn one raw model object into an `Extracted`, or refuse it.

    Every rejection here is a case where storing the entity would be worse than
    losing it: an unknown type breaks grouping, an out-of-range confidence
    breaks ranking, and a citation we did not supply is a fabricated link to
    evidence that does not exist.
    """
    name = canonical_label(str(raw.get("name") or ""))
    if not name:
        raise InvalidEntity("entity has no name")

    entity_type = str(raw.get("entity_type") or "")
    if not is_valid_type(entity_type):
        raise InvalidEntity(f"{name!r}: unknown entity_type {entity_type!r}")

    try:
        confidence = float(raw.get("confidence"))
    except (TypeError, ValueError):
        raise InvalidEntity(f"{name!r}: confidence is not a number") from None
    if not 0.0 <= confidence <= 1.0:
        raise InvalidEntity(f"{name!r}: confidence {confidence} out of range")

    paragraph_id = str(raw.get("paragraph_id") or "")
    if paragraph_id not in valid_paragraph_ids:
        raise InvalidEntity(
            f"{name!r}: cites paragraph {paragraph_id!r}, which was not supplied")

    quote = str(raw.get("quote") or "").strip()
    if not quote:
        raise InvalidEntity(f"{name!r}: no supporting quote")

    value = raw.get("value")
    if value is not None:
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise InvalidEntity(f"{name!r}: value is not a number") from None

    description = raw.get("description")
    return Extracted(
        name=name,
        entity_type=entity_type,
        confidence=confidence,
        paragraph_id=paragraph_id,
        quote=quote,
        description=canonical_label(str(description)) if description else None,
        period=str(raw["period"]) if raw.get("period") else None,
        value=value,
        unit=str(raw["unit"]) if raw.get("unit") else None,
    )
