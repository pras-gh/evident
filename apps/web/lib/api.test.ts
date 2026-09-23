import { describe, expect, it, vi } from "vitest";
import { RESOLVE_BATCH, resolveCitations } from "./api";
import type { CitationRef, ResolvedCitation } from "./types";

/** Stands in for POST /v1/evidence/resolve: refuses more than the API does,
 *  and answers each citation by position, as the API does. */
function fakePost() {
  return vi.fn(async (_path: string, body: unknown) => {
    const { citations } = body as { citations: CitationRef[] };
    if (citations.length > RESOLVE_BATCH) throw new Error("422 — too many citations");
    return {
      citations: citations.map(
        (c, index): ResolvedCitation => ({
          index,
          resolved: c.chunk_id !== 0,
          error: c.chunk_id === 0 ? "no chunk 0" : null,
          evidence: null,
        }),
      ),
    };
  });
}

const refs = (n: number): CitationRef[] =>
  Array.from({ length: n }, (_, i) => ({ chunk_id: i + 1, paragraph_id: null }));

describe("resolveCitations", () => {
  it("makes one call for up to the API's limit", async () => {
    const post = fakePost();
    const out = await resolveCitations(refs(RESOLVE_BATCH), post as never);
    expect(post).toHaveBeenCalledTimes(1);
    expect(out).toHaveLength(RESOLVE_BATCH);
  });

  it("splits more than that into batches the API accepts", async () => {
    const post = fakePost();
    const out = await resolveCitations(refs(2 * RESOLVE_BATCH + 7), post as never);
    expect(post).toHaveBeenCalledTimes(3);
    expect(out).toHaveLength(2 * RESOLVE_BATCH + 7);
  });

  it("numbers results by position in the whole list, not within a batch", async () => {
    const out = await resolveCitations(refs(250), fakePost() as never);
    expect(out.map((c) => c.index)).toEqual(Array.from({ length: 250 }, (_, i) => i));
  });

  it("keeps a bad citation in place in a later batch", async () => {
    const citations = refs(150);
    citations[120] = { chunk_id: 0, paragraph_id: null };
    const out = await resolveCitations(citations, fakePost() as never);
    expect(out[120]).toMatchObject({ index: 120, resolved: false, error: "no chunk 0" });
    expect(out.filter((c) => !c.resolved)).toHaveLength(1);
  });

  it("makes no call for no citations", async () => {
    const post = fakePost();
    expect(await resolveCitations([], post as never)).toEqual([]);
    expect(post).not.toHaveBeenCalled();
  });
});
