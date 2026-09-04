"""The canonical taxonomy, the Pydantic gate, and the extraction guardrails.

The tests that matter here are the refusals. Extraction quality is a prompt
question and cannot be asserted offline, but *what we refuse to store* is pure
logic, and it is the difference between "every answer is backed by evidence"
being enforced and being a slogan.
"""
from __future__ import annotations

import json
import unittest

from evident_ai.extract import (DropReport, Extraction, ExtractionRejected,
                                cache_hit_rate, collect_batch, extract_document,
                                extract_from_blocks, parse_response, render,
                                request_params, validate_entities,
                                validate_relationships)
from evident_ai.prompts import EXTRACT_ENTITIES
from evident_ai.schema import (EntityExtractionResponse, ExtractedEntity,
                               wire_schema)
from evident_graph.taxonomy import (ENTITY_TYPES, RELATIONSHIP_NAMES,
                                    TYPE_NAMES, check_constraint, slug)
from evident_parser.models import Block
from pydantic import ValidationError

VALID = {"7_1", "7_2"}


def ent(**over):
    base = {"name": "Blackwell", "entity_type": "product", "confidence": 0.99,
            "paragraph_id": "7_1", "quote": "accelerate Blackwell deployment"}
    base.update(over)
    return base


def rel(**over):
    base = {"source_name": "AI Infrastructure", "target_name": "Blackwell",
            "relationship_type": "drives_investment", "confidence": 0.9,
            "paragraph_id": "7_1", "quote": "investing in AI infrastructure"}
    base.update(over)
    return base


class Taxonomy(unittest.TestCase):
    def test_the_set_is_the_eight_specified_types(self):
        self.assertEqual(
            set(TYPE_NAMES),
            {"strategy", "product", "executive", "risk", "metric", "segment",
             "company", "geography"})

    def test_every_type_carries_a_definition_and_examples(self):
        for t in ENTITY_TYPES:
            self.assertTrue(t.definition.strip(), t.name)
            self.assertTrue(t.examples, t.name)

    def test_check_constraint_covers_exactly_the_canonical_types(self):
        sql = check_constraint("entity_type")
        for name in TYPE_NAMES:
            self.assertIn(f"'{name}'", sql)
        self.assertEqual(sql.count("'"), len(TYPE_NAMES) * 2)

    def test_relationship_types_are_closed_and_exclude_the_derived_one(self):
        # co_occurs is computed from shared documents, never asserted; letting
        # the model emit it would make a derived edge look like a claim
        self.assertNotIn("co_occurs", RELATIONSHIP_NAMES)
        self.assertIn("competes_with", RELATIONSHIP_NAMES)

    def test_slug_keeps_underscores_because_node_ids_are_frozen(self):
        self.assertEqual(slug("AI Infrastructure"), "ai_infrastructure")
        self.assertNotIn("-", slug("Cost Optimization"))

    def test_slug_folds_the_variants_a_filing_actually_uses(self):
        self.assertEqual(slug("Accelerated Computing"), slug("AI Infrastructure"))


class WireSchema(unittest.TestCase):
    """The request's schema is generated from the models it validates against."""

    def setUp(self):
        self.s = wire_schema()
        self.item = self.s["properties"]["entities"]["items"]

    def test_is_self_contained(self):
        # refs would leave how much of JSON Schema the endpoint resolves an
        # open question; the schema is small enough that inlining costs nothing
        blob = json.dumps(self.s)
        self.assertNotIn("$ref", blob)
        self.assertNotIn("$defs", blob)

    def test_forbids_fields_we_did_not_ask_for(self):
        self.assertIs(self.item["additionalProperties"], False)

    def test_enums_and_bounds_come_from_the_canonical_tuples(self):
        self.assertEqual(self.item["properties"]["entity_type"]["enum"],
                         list(TYPE_NAMES))
        self.assertEqual(
            self.s["properties"]["relationships"]["items"]["properties"]
            ["relationship_type"]["enum"], list(RELATIONSHIP_NAMES))
        conf = self.item["properties"]["confidence"]
        self.assertEqual((conf["minimum"], conf["maximum"]), (0.0, 1.0))

    def test_optional_fields_are_plain_types_not_nullable_unions(self):
        self.assertEqual(self.item["properties"]["description"]["type"], "string")
        self.assertNotIn("description", self.item["required"])

    def test_prompt_and_schema_describe_the_same_vocabulary(self):
        for name in list(TYPE_NAMES) + list(RELATIONSHIP_NAMES):
            self.assertIn(name, EXTRACT_ENTITIES.system)


class PydanticGate(unittest.TestCase):
    """Nothing becomes an object except through EntityExtractionResponse."""

    def ok(self, payload):
        return EntityExtractionResponse.model_validate_json(json.dumps(payload))

    def test_accepts_a_well_formed_response(self):
        r = self.ok({"entities": [ent()], "relationships": [rel()]})
        self.assertEqual(r.entities[0].name, "Blackwell")
        self.assertEqual(r.relationships[0].relationship_type, "drives_investment")

    def test_missing_lists_default_to_empty_rather_than_failing(self):
        r = self.ok({})
        self.assertEqual((r.entities, r.relationships), ([], []))

    def test_rejects_a_type_outside_the_canonical_set(self):
        with self.assertRaises(ValidationError):
            self.ok({"entities": [ent(entity_type="theme")]})

    def test_rejects_a_relationship_type_outside_the_canonical_set(self):
        with self.assertRaises(ValidationError):
            self.ok({"entities": [ent()], "relationships": [rel(relationship_type="rivals")]})

    def test_rejects_confidence_outside_zero_to_one(self):
        for bad in (1.5, -0.2):
            with self.assertRaises(ValidationError):
                self.ok({"entities": [ent(confidence=bad)]})

    def test_rejects_empty_name_or_quote(self):
        with self.assertRaises(ValidationError):
            self.ok({"entities": [ent(name="")]})
        with self.assertRaises(ValidationError):
            self.ok({"entities": [ent(quote="")]})

    def test_rejects_a_field_we_never_asked_for(self):
        # extra="forbid": a field we do not model is a response we do not
        # understand, not one to silently discard part of
        with self.assertRaises(ValidationError):
            self.ok({"entities": [ent(sentiment="bullish")]})

    def test_rejects_malformed_json_outright(self):
        with self.assertRaises(ValidationError):
            EntityExtractionResponse.model_validate_json('{"entities": [')

    def test_metric_carries_its_reading_when_the_text_states_one(self):
        r = self.ok({"entities": [ent(name="Revenue", entity_type="metric",
                                      period="FY2025", value=130497,
                                      unit="USD millions")]})
        e = r.entities[0]
        self.assertEqual((e.period, e.value, e.unit),
                         ("FY2025", 130497.0, "USD millions"))


class PostValidation(unittest.TestCase):
    """The two rules a schema cannot express, because they depend on our input."""

    def test_an_entity_citing_a_paragraph_we_never_sent_is_dropped(self):
        report = DropReport()
        kept = validate_entities(
            [ExtractedEntity(**ent()), ExtractedEntity(**ent(name="Ghost",
                                                             paragraph_id="99_9"))],
            VALID, report)
        self.assertEqual([e.name for e in kept], ["Blackwell"])
        self.assertEqual((report.kept, report.dropped), (1, 1))
        self.assertIn("99_9", " ".join(report.bad_ids))
        self.assertIn("not supplied", " ".join(report.reasons))

    def test_an_edge_to_something_that_is_not_an_entity_is_dropped(self):
        from evident_ai.schema import ExtractedRelationship
        entities = [ExtractedEntity(**ent())]           # only Blackwell
        report = DropReport()
        kept = validate_relationships(
            [ExtractedRelationship(**rel())],            # cites AI Infrastructure
            entities, VALID, report)
        self.assertEqual(kept, [])
        self.assertIn("are not entities", " ".join(report.reasons))

    def test_an_edge_whose_endpoints_both_exist_survives(self):
        from evident_ai.schema import ExtractedRelationship
        entities = [ExtractedEntity(**ent()),
                    ExtractedEntity(**ent(name="AI Infrastructure",
                                          entity_type="strategy"))]
        report = DropReport()
        kept = validate_relationships([ExtractedRelationship(**rel())],
                                      entities, VALID, report)
        self.assertEqual(len(kept), 1)

    def test_endpoints_are_matched_on_slug_not_exact_string(self):
        # the model may say "Blackwell" in entities and "the Blackwell
        # platform" in an edge; both fold to the same node
        from evident_ai.schema import ExtractedRelationship
        entities = [ExtractedEntity(**ent()),
                    ExtractedEntity(**ent(name="Accelerated Computing",
                                          entity_type="strategy"))]
        report = DropReport()
        kept = validate_relationships(
            [ExtractedRelationship(**rel(source_name="AI Infrastructure"))],
            entities, VALID, report)
        self.assertEqual(len(kept), 1, "Accelerated Computing folds to "
                                       "ai_infrastructure and should match")

    def test_a_self_edge_is_dropped(self):
        from evident_ai.schema import ExtractedRelationship
        entities = [ExtractedEntity(**ent())]
        report = DropReport()
        kept = validate_relationships(
            [ExtractedRelationship(**rel(source_name="Blackwell",
                                         target_name="Blackwell"))],
            entities, VALID, report)
        self.assertEqual(kept, [])
        self.assertIn("self-edge", " ".join(report.reasons))


# --------------------------------------------------------------- fake client
class _Text:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Response:
    def __init__(self, payload, *, stop_reason="end_turn", thinking=True,
                 raw=None, stop_details=None):
        text = raw if raw is not None else json.dumps(payload)
        self.usage = type("U", (), {"input_tokens": 100, "output_tokens": 50,
                                    "cache_read_input_tokens": 0,
                                    "cache_creation_input_tokens": 0})()
        self.content = [_Text(text)]
        if thinking:   # responses lead with thinking; JSON is not at index 0
            self.content.insert(0, type("T", (), {"type": "thinking"})())
        self.stop_reason = stop_reason
        self.stop_details = stop_details


class _Client:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    class _Messages:
        def __init__(self, outer):
            self.outer = outer

        def create(self, **kw):
            self.outer.calls.append(kw)
            r = self.outer.responses
            return r.pop(0) if len(r) > 1 else r[0]

    @property
    def messages(self):
        return self._Messages(self)


class Rejection(unittest.TestCase):
    """A response that cannot be trusted whole produces nothing at all."""

    def test_truncated_response_is_rejected_not_salvaged(self):
        r = _Response(None, stop_reason="max_tokens",
                      raw='{"entities": [{"name": "Blac')
        with self.assertRaises(ExtractionRejected) as cm:
            parse_response(r)
        self.assertIn("truncated", cm.exception.reason)

    def test_refusal_is_rejected_and_records_the_category(self):
        r = _Response(None, stop_reason="refusal", raw="",
                      stop_details=type("D", (), {"category": "cyber"})())
        with self.assertRaises(ExtractionRejected) as cm:
            parse_response(r)
        self.assertIn("cyber", cm.exception.reason)

    def test_malformed_json_is_rejected_and_the_raw_text_is_kept(self):
        r = _Response(None, raw='{"entities": [oops]}')
        with self.assertRaises(ExtractionRejected) as cm:
            parse_response(r)
        self.assertEqual(cm.exception.raw, '{"entities": [oops]}')

    def test_a_single_bad_entity_rejects_the_whole_response(self):
        # the API is told the enum, so an unknown type means the response is
        # not what we asked for -- not that one row should be skipped
        r = _Response({"entities": [ent(), ent(entity_type="theme")]})
        with self.assertRaises(ExtractionRejected):
            parse_response(r)

    def test_no_text_block_is_rejected(self):
        r = _Response({"entities": []})
        r.content = [type("T", (), {"type": "thinking"})()]
        with self.assertRaises(ExtractionRejected):
            parse_response(r)


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.blocks = [Block(paragraph_id="7_1", ordinal=0, page_number=7,
                             text="NVIDIA continues investing in AI infrastructure "
                                  "to accelerate Blackwell deployment despite "
                                  "export restrictions into China.")]

    def test_the_worked_example_from_the_spec(self):
        client = _Client(_Response({"entities": [
            ent(),
            ent(name="AI Infrastructure", entity_type="strategy", confidence=0.97),
            ent(name="China", entity_type="geography", confidence=0.98),
            ent(name="Export Restrictions", entity_type="risk", confidence=0.95),
        ], "relationships": [rel()]}))
        out = extract_from_blocks(self.blocks, client=client)
        self.assertEqual(
            {(e.name, e.entity_type) for e in out.entities},
            {("Blackwell", "product"), ("AI Infrastructure", "strategy"),
             ("China", "geography"), ("Export Restrictions", "risk")})
        self.assertEqual(len(out.relationships), 1)
        self.assertEqual(out.report.dropped, 0)

    def test_a_hallucinated_citation_never_reaches_the_caller(self):
        client = _Client(_Response({"entities": [
            ent(), ent(name="Invented", paragraph_id="412_9")]}))
        out = extract_from_blocks(self.blocks, client=client)
        self.assertEqual([e.name for e in out.entities], ["Blackwell"])
        self.assertEqual(out.report.dropped, 1)

    def test_no_blocks_makes_no_request(self):
        client = _Client(_Response({"entities": []}))
        out = extract_from_blocks([], client=client)
        self.assertEqual((out.entities, client.calls), ([], []))

    def test_the_request_carries_the_generated_schema_and_a_cacheable_prompt(self):
        client = _Client(_Response({"entities": []}))
        extract_from_blocks(self.blocks, client=client)
        call = client.calls[0]
        self.assertEqual(call["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(call["output_config"]["format"]["schema"], wire_schema())

    def test_the_cached_prefix_is_identical_across_calls(self):
        client = _Client(_Response({"entities": []}))
        other = [Block(paragraph_id="7_2", ordinal=1, page_number=7, text="Other.")]
        extract_from_blocks(self.blocks, client=client)
        extract_from_blocks(other, client=client)
        self.assertEqual(client.calls[0]["system"], client.calls[1]["system"])
        self.assertNotEqual(client.calls[0]["messages"], client.calls[1]["messages"])

    def test_rendered_input_carries_the_paragraph_ids_the_model_must_cite(self):
        self.assertTrue(render(self.blocks).startswith("[7_1] "))

    def test_a_rejected_chunk_does_not_stop_the_document(self):
        groups = {"a": self.blocks, "b": self.blocks}
        client = _Client(_Response(None, stop_reason="max_tokens", raw="{"),
                         _Response({"entities": [ent()]}))
        out, report, usage = extract_document(groups, client=client)
        self.assertEqual(list(out), ["b"])
        self.assertEqual(report.rejected, 1)
        self.assertIn("a:", " ".join(report.reasons))
        # both chunks are counted: the rejected one was still generated and
        # still billed, and hiding that would understate exactly the runs you
        # most want to notice
        self.assertEqual(usage.requests, 2)


class Batch(unittest.TestCase):
    class _Result:
        def __init__(self, custom_id, payload, status="succeeded", **kw):
            self.custom_id = custom_id
            self.result = type("R", (), {
                "type": status,
                "message": _Response(payload, **kw) if payload is not None else None})()

    def setUp(self):
        self.groups = {
            "a": [Block(paragraph_id="1_1", ordinal=0, page_number=1, text="A.")],
            "b": [Block(paragraph_id="2_1", ordinal=0, page_number=2, text="B.")],
        }

    def test_results_are_matched_by_custom_id_not_by_position(self):
        results = [
            self._Result("b", {"entities": [ent(name="Gaming",
                                                entity_type="segment",
                                                paragraph_id="2_1", quote="B.")]}),
            self._Result("a", {"entities": [ent(name="CUDA", paragraph_id="1_1",
                                                quote="A.")]}),
        ]
        out, report = collect_batch(results, self.groups)
        self.assertEqual([e.name for e in out["a"].entities], ["CUDA"])
        self.assertEqual([e.name for e in out["b"].entities], ["Gaming"])
        self.assertEqual(report.dropped, 0)

    def test_a_failed_request_is_recorded_rather_than_silently_missing(self):
        out, report = collect_batch(
            [self._Result("a", None, status="errored")], self.groups)
        self.assertNotIn("a", out)
        self.assertEqual(report.rejected, 1)

    def test_a_truncated_batch_result_is_rejected_too(self):
        out, report = collect_batch(
            [self._Result("a", {}, stop_reason="max_tokens", raw="{")],
            self.groups)
        self.assertNotIn("a", out)
        self.assertEqual(report.rejected, 1)


class CacheReporting(unittest.TestCase):
    def test_hit_rate_distinguishes_a_hit_from_a_prompt_too_short_to_cache(self):
        hit = type("U", (), {"cache_read_input_tokens": 900, "input_tokens": 100,
                             "cache_creation_input_tokens": 0})()
        miss = type("U", (), {"cache_read_input_tokens": 0, "input_tokens": 1000,
                              "cache_creation_input_tokens": 0})()
        self.assertAlmostEqual(cache_hit_rate(hit), 0.9)
        self.assertEqual(cache_hit_rate(miss), 0.0)


if __name__ == "__main__":
    unittest.main()
