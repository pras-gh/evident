/** Only this site's own paths: a `next` parameter must never send anyone
 *  elsewhere (an open redirect). Shared by server pages and the sign-in card. */
export function safeNext(next: string | null | undefined, fallback = "/"): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) {
    return fallback;
  }
  return next;
}
