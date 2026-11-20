/** Citation links. A plain module — not "use client" — so server components
 *  (the evidence route, the timeline) and the viewer share one definition. */

/** `?cite=` for a citation: `{chunk_id}:{paragraph_id}`, or the chunk alone. */
export function citeParam(chunkId: number, paragraphId: string | null): string {
  return paragraphId ? `${chunkId}:${paragraphId}` : `${chunkId}`;
}

/** `12:27_3` → the citation it names; `12` → the chunk. */
export function parseCite(raw: string | null | undefined) {
  const m = raw?.match(/^(\d+)(?::(.+))?$/);
  return m ? { chunk_id: Number(m[1]), paragraph_id: m[2] ?? null } : null;
}

/** The evidence viewer's URL for a citation — what a link to it, or a reload,
 *  opens. Paragraph ids can carry `#` (`25_1#2`); URLSearchParams encodes it. */
export function evidenceUrl(chunkId: number, paragraphId: string | null,
                            entity?: string | null): string {
  const q = new URLSearchParams({ cite: citeParam(chunkId, paragraphId) });
  if (entity) q.set("entity", entity);
  return `/evidence/${chunkId}?${q}`;
}
