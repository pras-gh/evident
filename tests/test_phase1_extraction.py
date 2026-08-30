"""Phase 1 — the canonical taxonomy and the extraction guardrails.

The tests that matter here are the refusals. Extraction quality is a prompt
question and cannot be asserted offline, but *what we refuse to store* is pure
logic, and it is the difference between "every answer is backed by evidence"
being enforced and being a slogan.
"""
from __future__ import annotations

import json
import unittest

from evident_ai.extract import (DropReport, cache_hit_rate, collect_batch,
                                extract_from_blocks, json_schema, render,
                                validate_all)
from evident_ai.prompts import EXTRACT_ENTITIES
from evident_graph.taxonomy import (ENTITY_TYPES, TYPE_NAMES, InvalidEntity,
                                    check_constraint, slug, validate)
from evident_parser.models import Block

VALID = {"7_1", "7_2"}


def raw(**over):
    base = {"name": "Blackwell", "entity_type": "product", "confidence": 0.99,
            "paragraph_id": "7_1", "quote": "accelerate Blackwell deployment"}
    base.update(over)
    return base


class Taxonomy(unittest.TestCase):
    def test_the_set_is_the_eight_specified_types(self):
        self.assertEqual(
            set(TYPE_NAMES),
            {"strategy", "product", "executive", "risk", "metric", "segment",
             "company", "geography"})

    def test_every_type_carries_a_definition_and_examples(self):
        # they are rendered into the prompt; an empty one silently teaches
        # the model nothing about a type it is still allowed to return
        for t in ENTITY_TYPES:
            self.assertTrue(t.definition.strip(), t.name)
            self.assertTrue(t.examples, t.name)

    def test_check_constraint_covers_exactly_the_canonical_types(self):
        sql = check_constraint("entity_type")
        for name in TYPE_NAMES:
            self.assertIn(f"'{name}'", sql)
        self.assertEqual(sql.count("'"), len(TYPE_NAMES) * 2)

    def test_schema_enum_and_prompt_come_from_the_same_tuple(self):
        enum = json_schema()["properties"]["entities"]["items"] \
            ["properties"]["entity_type"]["enum"]
        self.assertEqual(enum, list(TYPE_NAMES))
        for name in TYPE_NAMES:
            self.assertIn(name, EXTRACT_ENTITIES.system)

    def test_slug_keeps_underscores_because_node_ids_are_frozen(self):
        # the graph contract's node ids are these strings; hyphens would break
        # every id a client has cached
        self.assertEqual(slug("AI Infrastructure"), "ai_infrastructure")
        self.assertNotIn("-", slug("Cost Optimization"))

    def test_slug_folds_the_variants_a_filing_actually_uses(self):
        self.assertEqual(slug("Accelerated Computing"), slug("AI Infrastructure"))
        self.assertEqual(slug("Export Restrictions"), slug("Export Control"))


class Validation(unittest.TestCase):
    def test_accepts_a_well_formed_entity(self):
        e = validate(raw(), valid_paragraph_ids=VALID)
        self.assertEqual((e.name, e.entity_type, e.slug),
                         ("Blackwell", "product", "blackwell"))

    def test_refuses_a_type_outside_the_canonical_set(self):
        with self.assertRaises(InvalidEntity):
            validate(raw(entity_type="theme"), valid_paragraph_ids=VALID)

    def test_refuses_a_citation_we_never_supplied(self):
        # the whole point: a fabricated citation looks exactly like a real one
        with self.assertRaises(InvalidEntity):
            validate(raw(paragraph_id="99_9"), valid_paragraph_ids=VALID)

    def test_refuses_confidence_outside_zero_to_one(self):
        for bad in (1.5, -0.2, "high", None):
            with self.assertRaises(InvalidEntity):
                validate(raw(confidence=bad), valid_paragraph_ids=VALID)

    def test_refuses_an_entity_with_no_name_or_no_quote(self):
        with self.assertRaises(InvalidEntity):
            validate(raw(name="  "), valid_paragraph_ids=VALID)
        with self.assertRaises(InvalidEntity):
            validate(raw(quote=""), valid_paragraph_ids=VALID)

    def test_metric_carries_its_reading_when_the_text_states_one(self):
        e = validate(raw(name="Revenue", entity_type="metric", period="FY2025",
                         value=130497, unit="USD millions"),
                     valid_paragraph_ids=VALID)
        self.assertEqual((e.period, e.value, e.unit),
                         ("FY2025", 130497.0, "USD millions"))

    def test_metric_without_a_figure_is_still_a_valid_entity(self):
        e = validate(raw(name="Gross Margin", entity_type="metric"),
                     valid_paragraph_ids=VALID)
        self.assertIsNone(e.value)


class Guardrail(unittest.TestCase):
    def test_survivors_are_kept_and_every_refusal_is_counted_with_a_reason(self):
        report = DropReport()
        kept = validate_all(
            [raw(),
             raw(name="Theme", entity_type="theme"),
             raw(name="Ghost", paragraph_id="99_9")],
            VALID, report)
        self.assertEqual([e.name for e in kept], ["Blackwell"])
        self.assertEqual((report.kept, report.dropped), (1, 2))
        self.assertIn("99_9", " ".join(report.bad_ids))
        self.assertEqual(len(report.reasons), 2)

    def test_drop_rate_is_reported_for_monitoring(self):
        report = DropReport(kept=3, dropped=1)
        self.assertAlmostEqual(report.drop_rate, 0.25)
        self.assertEqual(DropReport().drop_rate, 0.0)


# --------------------------------------------------------------- fake client
class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Response:
    stop_reason = "end_turn"

    def __init__(self, payload, thinking=True):
        content = [_Block(json.dumps(payload))]
        if thinking:
            # responses lead with thinking blocks; the parser must not assume
            # the JSON is at index 0
            content.insert(0, type("T", (), {"type": "thinking"})())
        self.content = content


class _Client:
    def __init__(self, payload):
        self.payload, self.calls = payload, []

    class _Messages:
        def __init__(self, outer):
            self.outer = outer

        def create(self, **kw):
            self.outer.calls.append(kw)
            return _Response(self.outer.payload)

    @property
    def messages(self):
        return self._Messages(self)


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.blocks = [Block(paragraph_id="7_1", ordinal=0, page_number=7,
                             text="NVIDIA continues investing in AI infrastructure "
                                  "to accelerate Blackwell deployment despite "
                                  "export restrictions into China.")]

    def test_the_worked_example_from_the_spec(self):
        client = _Client({"entities": [
            {"name": "Blackwell", "entity_type": "product", "confidence": 0.99,
             "paragraph_id": "7_1", "quote": "accelerate Blackwell deployment"},
            {"name": "AI Infrastructure", "entity_type": "strategy",
             "confidence": 0.97, "paragraph_id": "7_1",
             "quote": "investing in AI infrastructure"},
            {"name": "China", "entity_type": "geography", "confidence": 0.98,
             "paragraph_id": "7_1", "quote": "into China"},
            {"name": "Export Restrictions", "entity_type": "risk",
             "confidence": 0.95, "paragraph_id": "7_1",
             "quote": "despite export restrictions"},
        ]})
        got, report = extract_from_blocks(self.blocks, client=client)
        self.assertEqual(report.dropped, 0)
        self.assertEqual(
            {(e.name, e.entity_type) for e in got},
            {("Blackwell", "product"), ("AI Infrastructure", "strategy"),
             ("China", "geography"), ("Export Restrictions", "risk")})

    def test_a_hallucinated_citation_never_reaches_the_caller(self):
        client = _Client({"entities": [
            raw(),
            {"name": "Invented", "entity_type": "product", "confidence": 1.0,
             "paragraph_id": "412_9", "quote": "nothing said this"},
        ]})
        got, report = extract_from_blocks(self.blocks, client=client)
        self.assertEqual([e.name for e in got], ["Blackwell"])
        self.assertEqual(report.dropped, 1)

    def test_no_blocks_makes_no_request(self):
        client = _Client({"entities": []})
        got, report = extract_from_blocks([], client=client)
        self.assertEqual((got, report.kept, client.calls), ([], 0, []))

    def test_the_system_prompt_is_sent_as_a_cacheable_block(self):
        client = _Client({"entities": []})
        extract_from_blocks(self.blocks, client=client)
        system = client.calls[0]["system"]
        self.assertEqual(system[0]["cache_control"], {"type": "ephemeral"})

    def test_the_cached_prefix_is_identical_across_calls(self):
        # a per-request byte change would silently cost the cache on every chunk
        client = _Client({"entities": []})
        other = [Block(paragraph_id="7_2", ordinal=1, page_number=7, text="Other.")]
        extract_from_blocks(self.blocks, client=client)
        extract_from_blocks(other, client=client)
        self.assertEqual(client.calls[0]["system"], client.calls[1]["system"])
        self.assertNotEqual(client.calls[0]["messages"], client.calls[1]["messages"])

    def test_rendered_input_carries_the_paragraph_ids_the_model_must_cite(self):
        self.assertTrue(render(self.blocks).startswith("[7_1] "))


class Batch(unittest.TestCase):
    class _Result:
        def __init__(self, custom_id, payload, status="succeeded"):
            self.custom_id = custom_id
            self.result = type("R", (), {
                "type": status,
                "message": _Response(payload) if payload is not None else None})()

    def setUp(self):
        self.groups = {
            "a": [Block(paragraph_id="1_1", ordinal=0, page_number=1, text="A.")],
            "b": [Block(paragraph_id="2_1", ordinal=0, page_number=2, text="B.")],
        }

    def test_results_are_matched_by_custom_id_not_by_position(self):
        # the API returns them in arbitrary order; reading positionally would
        # attach one chunk's entities to another chunk's paragraph ids
        results = [
            self._Result("b", {"entities": [
                {"name": "Gaming", "entity_type": "segment", "confidence": 0.9,
                 "paragraph_id": "2_1", "quote": "B."}]}),
            self._Result("a", {"entities": [
                {"name": "CUDA", "entity_type": "product", "confidence": 0.9,
                 "paragraph_id": "1_1", "quote": "A."}]}),
        ]
        out, report = collect_batch(results, self.groups)
        self.assertEqual([e.name for e in out["a"]], ["CUDA"])
        self.assertEqual([e.name for e in out["b"]], ["Gaming"])
        self.assertEqual(report.dropped, 0)

    def test_a_failed_request_is_recorded_rather_than_silently_missing(self):
        out, report = collect_batch(
            [self._Result("a", None, status="errored")], self.groups)
        self.assertNotIn("a", out)
        self.assertIn("errored", " ".join(report.reasons))


class CacheReporting(unittest.TestCase):
    def test_hit_rate_distinguishes_a_hit_from_a_prompt_too_short_to_cache(self):
        hit = type("U", (), {"cache_read_input_tokens": 900,
                             "input_tokens": 100,
                             "cache_creation_input_tokens": 0})()
        miss = type("U", (), {"cache_read_input_tokens": 0,
                              "input_tokens": 1000,
                              "cache_creation_input_tokens": 0})()
        self.assertAlmostEqual(cache_hit_rate(hit), 0.9)
        self.assertEqual(cache_hit_rate(miss), 0.0)


if __name__ == "__main__":
    unittest.main()
