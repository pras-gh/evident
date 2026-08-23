"use client";

import type { CardDetail } from "@/lib/types";

/**
 * The full trail. Every revision shows its diff and the evidence behind it;
 * immaterial revisions stay in the list, tagged, rather than being filtered out.
 */
export function RevisionDrawer({
  card,
  onClose,
}: {
  card: CardDetail | null;
  onClose: () => void;
}) {
  if (!card) return null;

  return (
    <>
      <div className="scrim" onClick={onClose} aria-hidden />
      <aside className="drawer" role="dialog" aria-label={`${card.title} history`}>
        <header className="drawer-top">
          <div>
            <p className="drawer-title">{card.title}</p>
            <p className="drawer-src">
              Updates from <b>{card.source_label}</b> · {card.revision_count} revisions,{" "}
              {card.material_count} material
            </p>
          </div>
          <button className="drawer-x" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        <ol className="revs">
          {card.history
            .slice()
            .reverse()
            .map((r) => (
              <li key={r.revision} className={r.is_material ? "rev rev-material" : "rev"}>
                <div className="rev-head">
                  <span className="rev-n">rev {r.revision}</span>
                  <span className="rev-date">{r.as_of}</span>
                  <span className="rev-doc">{r.source_note}</span>
                  {!r.is_material && <span className="rev-tag">no change</span>}
                </div>
                <p className="rev-sum">{r.summary}</p>
                <ul className="rev-facts">
                  {r.facts.map((f) => (
                    <li key={f.key}>
                      {f.label}
                      {f.value ? ` — ${f.value}` : ""}
                      {f.status ? ` [${f.status}]` : ""}
                    </li>
                  ))}
                </ul>
                {r.evidence.map((e, i) => (
                  <p className="rev-ev" key={i}>
                    {e.form_type}
                    {e.page_number ? ` · p. ${e.page_number}` : ""}
                    {e.section_path.length ? ` · ${e.section_path.join(" › ")}` : ""}
                  </p>
                ))}
              </li>
            ))}
        </ol>
      </aside>
    </>
  );
}
