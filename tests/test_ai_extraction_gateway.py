"""Entity extraction through the AI gateway — its default path.

Extraction code is unchanged; with no client injected it now goes through the
gateway as task `financial_extraction`. These check that the route decides the
provider, that every call is logged against the organization, and that the
gateway's two refusals mean the right thing to a document: a chunk no provider
answered is rejected and the rest carry on; a usage limit stops the document.
"""
from __future__ import annotations

import json
import unittest
import uuid
from datetime import date, datetime, timezone

from parallx_ai.extract import ExtractionRejected, extract_document, extract_from_blocks
from parallx_ai.gateway import (AIContext, CacheSpec, Gateway, MemoryStore,
                                UsageLimitExceeded, set_gateway)
from parallx_ai.gateway.config import from_dict
from parallx_ai.gateway.providers.base import ErrorKind, ProviderError
from parallx_parser.models import Block
from test_ai_gateway import RAW, Script, ok

ORG = uuid.uuid4()
CTX = AIContext(organization_id=ORG, source="worker:test")
BLOCKS = [Block(paragraph_id="27_1", ordinal=0, page_number=27,
                text="Export controls restrict shipments of our products to China."),
          Block(paragraph_id="27_2", ordinal=1, page_number=27,
                text="We depend on TSMC to manufacture our GPUs.")]
ANSWER = json.dumps({"entities": [
    {"name": "Export Controls", "entity_type": "risk", "confidence": 0.95, "paragraph_id": "27_1",
     "quote": "Export controls restrict shipments of our products to China."},
    {"name": "China", "entity_type": "geography", "confidence": 0.9, "paragraph_id": "27_1",
     "quote": "to China"},
    {"name": "Invented", "entity_type": "risk", "confidence": 0.9, "paragraph_id": "99_9",
     "quote": "not in the input"},
], "relationships": []})


def install(routes=None, **scripts):
    raw = json.loads(json.dumps(RAW))
    # extraction asks for 16,000 output tokens; config/ai.toml allows exactly that
    raw["limits"]["max_output_tokens"] = 16_000
    if routes:
        raw["routes"].update(routes)
    store = MemoryStore(orgs={ORG: {}})
    providers = {n: scripts.get(n, Script(n)) for n in ("anthropic", "gemini", "groq")}
    g = Gateway(from_dict(raw, today=date(2026, 11, 21)), providers, store,
                now=lambda: datetime(2026, 11, 21, tzinfo=timezone.utc), sleep=lambda s: None)
    set_gateway(g)
    return store, providers


class ThroughTheGateway(unittest.TestCase):
    def tearDown(self):
        set_gateway(None)

    def test_the_route_picks_the_provider_and_the_call_is_logged(self):
        store, p = install(gemini=Script("gemini", ok(text=ANSWER, inp=3000, out=400)))
        out = extract_from_blocks(BLOCKS, context=CTX)
        self.assertEqual([e.name for e in out.entities], ["Export Controls", "China"])
        self.assertEqual(out.report.dropped, 1, "the citation guard still applies")
        self.assertEqual((out.model, out.cost_usd > 0), ("gemini:flash", True))
        [row] = store.rows
        self.assertEqual((row.task_type, row.organization_id, row.user_id, row.status),
                         ("financial_extraction", ORG, None, "success"))
        self.assertEqual((out.usage.input_tokens, out.usage.output_tokens), (3000, 400))

    def test_the_request_carries_the_schema_and_the_numbered_paragraphs(self):
        seen = []

        class Peek(Script):
            def complete(self, model, request, *, timeout, params):
                seen.append(request)
                return super().complete(model, request, timeout=timeout, params=params)

        install(gemini=Peek("gemini", ok(text=ANSWER)))
        extract_from_blocks(BLOCKS, context=CTX)
        [r] = seen
        self.assertIn("[27_1] Export controls", r.user)
        self.assertEqual(r.json_schema["type"], "object")
        self.assertIn("entities", r.json_schema["properties"])
        self.assertGreater(len(r.system), 1000)

    def test_switching_the_route_switches_the_provider_and_nothing_else(self):
        store, p = install(routes={"financial_extraction": ["anthropic:sonnet"]},
                           anthropic=Script("anthropic", ok(text=ANSWER)))
        out = extract_from_blocks(BLOCKS, context=CTX)
        self.assertEqual((out.model, p["gemini"].calls), ("anthropic:sonnet", []))

    def test_a_chunk_no_provider_answered_is_rejected_and_the_document_carries_on(self):
        # something in chunk "a" both providers reject; a bad request is the
        # chunk's fault, so it does not open either circuit and "b" goes through
        install(gemini=Script("gemini", bad_request(), ok(text=ANSWER)),
                groq=Script("groq", bad_request()))
        out, report, _ = extract_document({"a": BLOCKS, "b": BLOCKS}, context=CTX)
        self.assertEqual((report.rejected, list(out)), (1, ["b"]))
        self.assertIn("no provider answered", report.rejections[0])

    def test_a_usage_limit_stops_the_document(self):
        install()
        with self.assertRaises(UsageLimitExceeded):
            extract_document({"a": BLOCKS}, context=CTX, max_tokens=50_000)

    def test_an_unparseable_answer_is_rejected_with_its_model_and_cost(self):
        install(gemini=Script("gemini", ok(text="not json")))
        with self.assertRaises(ExtractionRejected) as cm:
            extract_from_blocks(BLOCKS, context=CTX)
        self.assertEqual(cm.exception.model, "gemini:flash")
        self.assertIsNotNone(cm.exception.cost_usd)

    def test_a_filing_extracted_before_is_answered_from_the_cache(self):
        store, p = install(gemini=Script("gemini", ok(text=ANSWER, inp=3000, out=400)))
        spec = CacheSpec("0001045810-25-000023", "extract_entities@3.1.0", shared=True)
        first, _, _ = extract_document({"a": BLOCKS}, context=CTX, cache=spec)
        again, _, usage = extract_document({"a": BLOCKS}, context=CTX, cache=spec)
        self.assertEqual(len(p["gemini"].calls), 1, "the rebuild paid for the chunk again")
        self.assertEqual([e.name for e in again["a"].entities],
                         [e.name for e in first["a"].entities])
        self.assertEqual((again["a"].cost_usd, usage.input_tokens), (0, 0))
        self.assertEqual([r.status for r in store.rows], ["success", "cached"])

        # another organization asking for the same public filing: also free
        other = AIContext(organization_id=uuid.uuid4(), source="worker:test")
        store.orgs[other.organization_id] = {}
        extract_document({"a": BLOCKS}, context=other, cache=spec)
        self.assertEqual(len(p["gemini"].calls), 1)

        # a new prompt version is a miss
        p["gemini"].outcomes.append(ok(text=ANSWER))
        extract_document({"a": BLOCKS}, context=CTX,
                         cache=CacheSpec(spec.accession, "extract_entities@3.2.0", shared=True))
        self.assertEqual(len(p["gemini"].calls), 2)

    def test_without_a_cache_spec_every_call_is_paid(self):
        _, p = install(gemini=Script("gemini", ok(text=ANSWER), ok(text=ANSWER)))
        extract_document({"a": BLOCKS}, context=CTX)
        extract_document({"a": BLOCKS}, context=CTX)
        self.assertEqual(len(p["gemini"].calls), 2)


def bad_request():
    return ProviderError(ErrorKind.BAD_REQUEST, "400: invalid argument")


if __name__ == "__main__":
    unittest.main()
