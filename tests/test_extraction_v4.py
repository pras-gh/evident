"""Extraction prompt v4: names that persist across filings, and evidence that
reads as evidence.

Measured on v3.1 (Gemini 3.5 Flash-Lite, the three demo 10-Ks): 84% of quotes
were under four words — the median was two, usually just the name — and the
same topic took a different name from one year to the next, which the
timeline then reported as dropped under one name and new under the other.

* a quote must be found in the paragraph it cites, and is widened to the
  sentence containing it, in code: deterministic, and free of output tokens;
* a name of more than MAX_NAME_WORDS words is a phrase, not a topic;
* each request lists the company's known topics that share a word with its
  paragraphs, so the model can reuse their names.
"""
from __future__ import annotations

import json
import unittest

from evident_ai.extract import (KNOWN_HEADER, KNOWN_PER_REQUEST, MAX_NAME_WORDS,
                                MAX_QUOTE_CHARS, DropReport, anchor,
                                extract_from_blocks, find, locate, relevant_known,
                                render, request_params, validate_entities,
                                validate_relationships)
from evident_ai.prompts import EXTRACT_ENTITIES
from evident_ai.schema import ExtractedEntity, ExtractedRelationship
from evident_parser import sentences
from evident_parser.models import Block

PARAGRAPH = ("In April 2025, the U.S. government informed us that a license is "
             "required for exports of our H20 integrated circuits to China. "
             "We recorded a $4.5 billion charge as a result. Sales of H20 "
             "products were $4.6 billion in the first quarter.")
BLOCK = Block(paragraph_id="26_1", ordinal=0, page_number=26, text=PARAGRAPH)


def entity(name="H20", quote="H20 integrated circuits", pid="26_1", **over):
    return ExtractedEntity(name=name, entity_type=over.pop("entity_type", "product"),
                           confidence=0.9, paragraph_id=pid, quote=quote, **over)


class Sentences(unittest.TestCase):
    def test_abbreviations_and_initials_do_not_end_a_sentence(self):
        text = ("The U.S. Government and Nvidia Corp. agreed, e.g. on No. 4. "
                "J. Smith signed it. Then it ended!")
        got = [text[s:e] for s, e in sentences.bounds(text)]
        self.assertEqual(got, ["The U.S. Government and Nvidia Corp. agreed, e.g. on No. 4.",
                               "J. Smith signed it.", "Then it ended!"])

    def test_a_closing_quote_or_bracket_stays_with_its_sentence(self):
        text = "It said “stop.” Then (as noted.) Next one."
        self.assertEqual([text[s:e] for s, e in sentences.bounds(text)],
                         ["It said “stop.”", "Then (as noted.)", "Next one."])

    def test_around_covers_every_sentence_the_span_touches(self):
        a, b = PARAGRAPH.index("license"), PARAGRAPH.index("charge") + 6
        s, e = sentences.around(PARAGRAPH, a, b)
        self.assertTrue(PARAGRAPH[s:e].startswith("In April 2025"))
        self.assertTrue(PARAGRAPH[s:e].endswith("charge as a result."))


class Quotes(unittest.TestCase):
    def test_a_span_becomes_its_sentence_as_the_paragraph_has_it(self):
        self.assertEqual(anchor("$4.5 billion charge", PARAGRAPH),
                         "We recorded a $4.5 billion charge as a result.")

    def test_a_copy_with_curly_quotes_case_or_spacing_differences_is_found(self):
        text = "Our “AI factories” are   growing — quickly."
        self.assertEqual(locate('our "ai factories" are growing - quickly', text),
                         (0, len(text) - 1))

    def test_a_quote_that_is_not_in_the_paragraph_is_not_anchored(self):
        self.assertIsNone(anchor("accelerate Blackwell deployment", PARAGRAPH))
        self.assertIsNone(anchor("", PARAGRAPH))
        self.assertIsNone(anchor("   ", PARAGRAPH))

    def test_a_quote_that_skips_words_is_found_fragment_by_fragment(self):
        """Seen on the demo 10-Ks: 7 of 12 unfound quotes elided with "...",
        every fragment verbatim."""
        quote = "a license is required... H20 integrated circuits\u2026 to China"
        start, end = locate(quote, PARAGRAPH)
        self.assertTrue(PARAGRAPH[start:end].startswith("a license is required"))
        self.assertTrue(PARAGRAPH[start:end].endswith("to China"))

    def test_fragments_must_appear_in_order(self):
        self.assertIsNone(locate("to China... a license is required", PARAGRAPH))

    def test_one_changed_word_is_a_misquote(self):
        """Also seen: "negative impact demand" for the filing's "negatively
        impact demand". Close is not verbatim; the item is dropped."""
        self.assertIsNone(locate("a licence is required", PARAGRAPH))

    def test_a_sentence_too_long_to_read_keeps_the_span(self):
        listing = "We sell " + ", ".join(f"product {i}" for i in range(200)) + "."
        self.assertGreater(len(listing), MAX_QUOTE_CHARS)
        self.assertEqual(anchor("product 17", listing), "product 17")


class Validation(unittest.TestCase):
    def setUp(self):
        self.report = DropReport()

    def validate(self, *entities):
        return validate_entities(entities, {"26_1"}, self.report, texts={"26_1": PARAGRAPH})

    def test_an_anchored_quote_is_stored_as_its_sentence(self):
        [e] = self.validate(entity(quote="H20"))
        self.assertTrue(e.quote.startswith("In April 2025, the U.S. government"))
        self.assertTrue(e.quote.endswith("to China."))

    def test_an_unanchored_quote_costs_its_entity_with_a_reason(self):
        kept = self.validate(entity(), entity(name="Blackwell", quote="Blackwell ramp"))
        self.assertEqual([e.name for e in kept], ["H20"])
        self.assertEqual((self.report.kept, self.report.dropped), (1, 1))
        self.assertIn("'Blackwell': quote is not in paragraph 26_1", self.report.reasons)

    def test_a_phrase_is_not_a_topic_name(self):
        phrase = "loss of data or other information technology assets"
        kept = self.validate(entity(name=phrase, quote="H20"),
                             entity(name="Country Groups D1, D4, and D5", quote="H20"))
        self.assertEqual([e.name for e in kept], ["Country Groups D1, D4, and D5"],
                         f"{MAX_NAME_WORDS} words is still a name")
        self.assertIn("a phrase, not a topic's name", self.report.reasons[0])

    def test_a_quote_in_another_part_of_the_same_paragraph_moves_the_citation(self):
        texts = {"28_3#1": "Export controls may hurt demand in Europe.",
                 "28_3#2": "They may also hurt demand in Latin America.",
                 "29_1": "Climate change may affect our operations."}
        self.assertEqual(find("hurt demand in Europe", "28_3#2", texts),
                         ("28_3#1", "Export controls may hurt demand in Europe."))
        self.assertIsNone(find("Climate change", "28_3#2", texts),
                          "another paragraph is another citation, not a repair")
        kept = validate_entities([entity(name="Europe", entity_type="geography",
                                         quote="demand in Europe", pid="28_3#2")],
                                 set(texts), self.report, texts=texts)
        self.assertEqual(kept[0].paragraph_id, "28_3#1")

    def test_relationship_quotes_are_anchored_too(self):
        ents = self.validate(entity(), entity(name="China", entity_type="geography",
                                              quote="to China"))
        rel = dict(source_name="China", target_name="H20", relationship_type="constrains",
                   confidence=0.8, paragraph_id="26_1")
        kept = validate_relationships(
            [ExtractedRelationship(**rel, quote="exports of our H20"),
             ExtractedRelationship(**rel, quote="shipments to Russia")],
            ents, {"26_1"}, self.report, texts={"26_1": PARAGRAPH})
        self.assertEqual(len(kept), 1)
        self.assertTrue(kept[0].quote.startswith("In April 2025"))

    def test_without_texts_quotes_are_left_as_returned(self):
        kept = validate_entities([entity(quote="anything")], {"26_1"}, self.report)
        self.assertEqual(kept[0].quote, "anything")


class KnownTopics(unittest.TestCase):
    KNOWN = [("Export Controls", "risk"), ("China", "geography"), ("Tariffs", "risk"),
             ("H20", "product"), ("Data Center", "segment"), ("AI", "strategy")]

    def test_topics_sharing_a_word_with_the_paragraphs_are_listed_in_order(self):
        block = Block(paragraph_id="1_1", ordinal=0, page_number=1,
                      text="New export restrictions apply to our H20 sales in China.")
        self.assertEqual(relevant_known(self.KNOWN, [block]),
                         [("Export Controls", "risk"), ("China", "geography"),
                          ("H20", "product")])

    def test_common_words_alone_do_not_make_a_topic_relevant(self):
        known = [("Data Center", "segment"), ("Product Risks", "risk")]
        block = Block(paragraph_id="1_1", ordinal=0, page_number=1,
                      text="Our products carry data and other risks.")
        self.assertEqual(relevant_known(known, [block]), [],
                         "'data', 'products' and 'risks' are in nearly every paragraph")
        block = Block(paragraph_id="1_1", ordinal=0, page_number=1,
                      text="A new data center opened.")
        self.assertEqual(relevant_known(known, [block]), [("Data Center", "segment")])

    def test_a_short_name_is_matched_on_its_own_words(self):
        block = Block(paragraph_id="1_1", ordinal=0, page_number=1, text="We use AI.")
        self.assertIn(("AI", "strategy"), relevant_known(self.KNOWN, [block]))

    def test_at_most_a_fixed_number_are_listed(self):
        many = [(f"Topic{i} Export", "risk") for i in range(100)]
        block = Block(paragraph_id="1_1", ordinal=0, page_number=1, text="export")
        self.assertEqual(len(relevant_known(many, [block])), KNOWN_PER_REQUEST)

    def test_the_request_lists_them_before_the_paragraphs(self):
        user = request_params([BLOCK], known=self.KNOWN)["messages"][0]["content"]
        self.assertTrue(user.startswith(KNOWN_HEADER))
        self.assertIn("- China (geography)\n- H20 (product)", user)
        self.assertNotIn("Tariffs", user)
        self.assertTrue(user.endswith(f"Paragraphs:\n\n[26_1] {PARAGRAPH}"))

    def test_with_none_known_the_request_is_the_paragraphs_alone(self):
        self.assertEqual(render([BLOCK]), f"[26_1] {PARAGRAPH}")
        self.assertEqual(request_params([BLOCK])["messages"][0]["content"],
                         f"[26_1] {PARAGRAPH}")


class Prompt(unittest.TestCase):
    def test_v4_asks_for_short_reused_names(self):
        self.assertEqual(EXTRACT_ENTITIES.version, "4.1.0")
        system = EXTRACT_ENTITIES.system
        self.assertIn("one to five words", system)
        self.assertIn("use that topic's name exactly as listed", system)
        self.assertIn("Quotes are checked against the paragraph", system)
        self.assertIn("separate entities", system)

    def test_examples_are_not_taken_from_the_filings_it_is_measured_on(self):
        """v4.0 used "H20 Inventory Charge" as its example, and the model
        returned exactly that for the paragraph it came from — and not H20.
        Examples drawn from the evaluation corpus shape the output being
        measured."""
        rules = EXTRACT_ENTITIES.system.split("Worked example.")[0]
        for phrase in ("H20", "AI Diffusion", "Product, system security", "GB200"):
            self.assertNotIn(phrase, rules)


class EndToEnd(unittest.TestCase):
    """Through extract_from_blocks with a client that answers like v3.1 did:
    bare-name quotes, and a name coined from this year's wording."""

    def test_short_quotes_come_back_as_sentences(self):
        answer = {"entities": [
            {"name": "H20", "entity_type": "product", "confidence": 0.95,
             "paragraph_id": "26_1", "quote": "H20"},
            {"name": "H20 Inventory Charge", "entity_type": "metric", "confidence": 0.9,
             "paragraph_id": "26_1", "quote": "$4.5 billion charge"}],
            "relationships": []}
        seen = []

        class Client:
            @property
            def messages(self):
                return self

            def create(self, **params):
                seen.append(params)
                text = type("T", (), {"type": "text", "text": json.dumps(answer)})()
                return type("R", (), {"content": [text], "stop_reason": "end_turn",
                                      "usage": None})()

        out = extract_from_blocks([BLOCK], client=Client(), known=[("H20", "product")])
        self.assertIn("- H20 (product)", seen[0]["messages"][0]["content"])
        self.assertEqual([e.quote for e in out.entities],
                         [PARAGRAPH[:PARAGRAPH.index(" We recorded")],
                          "We recorded a $4.5 billion charge as a result."])


if __name__ == "__main__":
    unittest.main()
