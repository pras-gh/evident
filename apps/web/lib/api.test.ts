import { afterEach, describe, expect, it, vi } from "vitest";
import { RESOLVE_BATCH, resolveCitations, searchFilings } from "./api";
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

// ------------------------------------------------------------------ search

function respond(status: number, body: unknown) {
  return vi.fn(async () => new Response(JSON.stringify(body), { status }));
}

describe("searchFilings", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("posts the query, uncached, and returns the hits", async () => {
    const fetch = respond(200, { query: "chips", hits: [], took_ms: 5, embedder: "gemini/x" });
    vi.stubGlobal("fetch", fetch);
    const out = await searchFilings("chips", "nvda", 5);
    expect(out).toEqual({ ok: true, result: { query: "chips", hits: [], took_ms: 5,
                                              embedder: "gemini/x" } });
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/search$/);
    expect(init.method).toBe("POST");
    expect(init.cache).toBe("no-store");
    expect(JSON.parse(init.body as string)).toEqual({ query: "chips", k: 5, ticker: "nvda" });
  });

  it("passes the API's reason through when search cannot answer", async () => {
    vi.stubGlobal("fetch", respond(503, { error: "search_not_indexed",
                                          message: "Run workers/embedding_worker.py" }));
    expect(await searchFilings("chips")).toEqual({
      ok: false, error: "search_not_indexed", message: "Run workers/embedding_worker.py" });
  });

  it("explains a query the API refuses as too short", async () => {
    vi.stubGlobal("fetch", respond(422, { detail: [{ type: "string_too_short" }] }));
    expect(await searchFilings("x")).toMatchObject({ ok: false, error: "too_short" });
  });

  it("says when the API is not running at all", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("fetch failed"); }));
    expect(await searchFilings("chips")).toMatchObject({ ok: false, error: "unreachable" });
  });
});
