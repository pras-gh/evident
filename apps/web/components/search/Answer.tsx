import type { ReactNode } from "react";
import { askQuestion } from "@/lib/api";
import { evidenceUrl } from "@/lib/cite";
import type { AskResponse } from "@/lib/types";
import { filedLabel } from "./SearchView";

/**
 * The answer above the passages: from the live web, or from the filings —
 * never both in one answer. A web answer is shown as Google returned it, with
 * its sources and its Search Suggestions (Google's terms require them, and
 * forbid modifying the answer or mixing other content into it). A filings
 * answer cites its passages, and each citation opens the paragraph.
 */
export async function AnswerCard({ query, kind, chunkIds }: {
  query: string;
  kind: string | null | undefined;
  chunkIds: number[];
}) {
  const answer = await askQuestion(query, kind, chunkIds);
  return answer ? <AnswerView answer={answer} /> : null;
}

export function AnswerPending() {
  return (
    <section className="ans ans-pending" aria-busy="true" aria-label="Answer">
      <p className="ans-kicker">Answer</p>
      <p className="ans-wait">Writing an answer… this can take ten seconds or so.</p>
    </section>
  );
}

export function AnswerView({ answer }: { answer: AskResponse }) {
  const notice = answer.notice && (
    <p className="srch-note" role="status">
      {answer.notice}
      {answer.limit_reached && <> <a href="/pricing">See plans</a></>}
    </p>
  );
  if (answer.mode === "none" || !answer.answer) return notice || null;

  if (answer.mode === "web") {
    return (
      <>
        {notice}
        <section className="ans" aria-label="Answer from the web">
          <p className="ans-kicker">Answer · from the web, via Google Search</p>
          <Body text={answer.answer} />
          {answer.sources.length > 0 && (
            <ol className="ans-sources" aria-label="Sources">
              {answer.sources.map((s, i) => (
                <li key={`${s.uri}-${i}`}>
                  <a href={s.uri} target="_blank" rel="noopener noreferrer">{s.title}</a>
                </li>
              ))}
            </ol>
          )}
          {answer.search_suggestions && (
            <iframe className="ans-suggest" title="Google Search suggestions"
                    sandbox="allow-popups allow-popups-to-escape-sandbox"
                    srcDoc={answer.search_suggestions} />
          )}
          <p className="ans-foot">Not from SEC filings · not investment advice</p>
        </section>
      </>
    );
  }

  const byN = new Map(answer.citations.map((c) => [c.n, c]));
  const companies = [...new Set(answer.citations.map((c) => c.company))];
  return (
    <>
      {notice}
      <section className="ans" aria-label="Answer from SEC filings">
        <p className="ans-kicker">
          Answer · from {companies.length ? `${companies.join(", ")}’s` : "the"} SEC filings
        </p>
        <Body text={answer.answer} cite={(n) => {
          const c = byN.get(n);
          return c ? evidenceUrl(c.chunk_id, c.paragraph_id) : null;
        }} />
        {answer.citations.length > 0 && (
          <ol className="ans-sources" aria-label="Cited passages">
            {[...answer.citations].sort((a, b) => a.n - b.n).map((c) => (
              <li key={c.n} value={c.n}>
                <a href={evidenceUrl(c.chunk_id, c.paragraph_id)}>
                  {c.company} {c.form_type}, {filedLabel(c.filed_at)} · {c.citation}
                </a>
              </li>
            ))}
          </ol>
        )}
        <p className="ans-foot">Each claim cites its paragraph · not investment advice</p>
      </section>
    </>
  );
}

type Cite = (n: number) => string | null;

/** Paragraphs and bullet lists, **bold** and [n] citations — the little
 *  markdown the models write — as elements, never as HTML. */
export function Body({ text, cite }: { text: string; cite?: Cite }) {
  const blocks: ReactNode[] = [];
  let items: string[] = [];
  const flush = () => {
    if (items.length) {
      blocks.push(<ul key={blocks.length}>{items.map((t, i) => <li key={i}>{inline(t, cite)}</li>)}</ul>);
      items = [];
    }
  };
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    const bullet = /^[*-]\s+(.*)$/.exec(line);
    if (bullet) { items.push(bullet[1]); continue; }
    flush();
    if (line) blocks.push(<p key={blocks.length}>{inline(line, cite)}</p>);
  }
  flush();
  return <div className="ans-body">{blocks}</div>;
}

function inline(text: string, cite?: Cite): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|\[\d{1,3}\])/g).filter(Boolean).map((part, i) => {
    const bold = /^\*\*([^*]+)\*\*$/.exec(part);
    if (bold) return <strong key={i}>{bold[1]}</strong>;
    const marker = /^\[(\d{1,3})\]$/.exec(part);
    if (marker && cite) {
      const href = cite(Number(marker[1]));
      if (href) return <a key={i} className="ans-cite" href={href}>{part}</a>;
    }
    return part;
  });
}
