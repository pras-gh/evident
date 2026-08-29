"""The only shape a Claude response is allowed to take.

Nothing reaches the database except through `EntityExtractionResponse`. There is
no path from raw model text to a row — no regex fallback, no partial salvage,
no "best effort" repair. A response either parses into these models or it is
rejected whole.

The same models generate the JSON Schema sent on the request, so the constraint
the API enforces and the constraint we validate against cannot drift. Writing
the wire schema by hand next to the Pydantic models is how you end up with an
`enum` the model obeys and a validator that disagrees with it.
"""
from __future__ import annotations

from typing import Any, Literal

from evident_graph.taxonomy import RELATIONSHIP_NAMES, TYPE_NAMES
from pydantic import BaseModel, ConfigDict, Field

# Subscripting Literal with a tuple is equivalent to listing the members, which
# keeps the canonical tuple as the single source. A type checker cannot follow
# it; the alternative is writing all eight names a third time, and a taxonomy
# that disagrees with itself is worse than one a linter cannot verify.
EntityTypeName = Literal[TYPE_NAMES]        # type: ignore[valid-type]
RelationshipTypeName = Literal[RELATIONSHIP_NAMES]  # type: ignore[valid-type]

#: `extra="forbid"` is what becomes `additionalProperties: false` on the wire.
#: Without it the model may return fields we never asked for, and they would be
#: silently discarded rather than refused.
STRICT = ConfigDict(extra="forbid")


class ExtractedEntity(BaseModel):
    model_config = STRICT

    name: str = Field(
        min_length=1, max_length=255,
        description="as the filing names it, not expanded or normalised")
    entity_type: EntityTypeName
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="how strongly the quoted span supports this entity: 1.0 "
                    "when stated outright, lower when implied. Do not inflate.")
    paragraph_id: str = Field(
        min_length=1,
        description="an id from the input, never invented")
    quote: str = Field(
        min_length=1,
        description="the verbatim span supporting this entity")
    description: str | None = Field(
        default=None, description="one clause on what it is, if the text says")

    # Metric readings. A metric entity is the *name* of a measure; these carry
    # a reading of it, which is a time series rather than a mention.
    period: str | None = Field(
        default=None, description="metric only: the period the figure covers")
    value: float | None = Field(
        default=None, description="metric only: the figure, if one is stated")
    unit: str | None = Field(
        default=None, description="metric only: e.g. USD millions, percent")


class ExtractedRelationship(BaseModel):
    model_config = STRICT

    # Endpoints are named, not id'd: the model has never seen our ids and
    # inventing them is not a thing we should give it the opportunity to do.
    # Names are resolved against this same response after validation.
    source_name: str = Field(min_length=1, max_length=255)
    target_name: str = Field(min_length=1, max_length=255)
    relationship_type: RelationshipTypeName
    confidence: float = Field(ge=0.0, le=1.0)
    paragraph_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class EntityExtractionResponse(BaseModel):
    """The whole response. Both lists come from one call.

    Relationships are extracted alongside entities so the model names an edge
    while the paragraph is still in front of it. Asking separately meant
    re-reading the same text to rediscover the endpoints.
    """
    model_config = STRICT

    entities: list[ExtractedEntity] = Field(default_factory=list)
    relationships: list[ExtractedRelationship] = Field(default_factory=list)


# --------------------------------------------------------------- wire schema
def _inline(node: Any, defs: dict[str, Any]) -> Any:
    """Resolve every `$ref` into the definition it points at.

    Pydantic emits nested models as `$defs` + `$ref`. Sending a self-contained
    schema removes any question of how much of the JSON Schema spec the
    endpoint resolves, and the schema is small enough that inlining costs
    nothing. `$defs` here are never recursive — an entity does not contain an
    entity — so this terminates.
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            target = dict(defs[ref.rsplit("/", 1)[1]])
            # keep siblings of the $ref (description, default, ...)
            target.update({k: v for k, v in node.items() if k != "$ref"})
            return _inline(target, defs)
        return {k: _inline(v, defs) for k, v in node.items() if k != "$defs"}
    if isinstance(node, list):
        return [_inline(v, defs) for v in node]
    return node


def _strip_nullable(node: Any) -> Any:
    """Rewrite Pydantic's `anyOf: [T, null]` optionals as plain `T`.

    An optional field is expressed by leaving it out of `required`, which the
    schema already does. Keeping the null branch as well invites the model to
    send an explicit `null` where omitting the key is what we want, and some
    schema validators reject `anyOf` alongside `additionalProperties: false`.
    """
    if isinstance(node, dict):
        any_of = node.get("anyOf")
        if isinstance(any_of, list):
            real = [b for b in any_of if b.get("type") != "null"]
            if len(real) == 1:
                merged = dict(real[0])
                merged.update({k: v for k, v in node.items()
                               if k not in ("anyOf", "default")})
                return _strip_nullable(merged)
        return {k: _strip_nullable(v) for k, v in node.items()
                if k != "default"}
    if isinstance(node, list):
        return [_strip_nullable(v) for v in node]
    return node


def wire_schema() -> dict[str, Any]:
    """The JSON Schema sent with the request, generated from the models."""
    raw = EntityExtractionResponse.model_json_schema()
    return _strip_nullable(_inline(raw, raw.get("$defs", {})))
