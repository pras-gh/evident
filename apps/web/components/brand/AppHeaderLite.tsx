import { ParallxMark } from "./ParallxMark";
import { ParallxWordmark } from "./ParallxWordmark";

/** The brand alone, for client pages that cannot run the full header. */
export function AppHeaderLite() {
  return (
    <header className="apphead">
      <div className="apphead-in">
        <a className="apphead-brand" href="/" aria-label="Parallx — home">
          <ParallxMark size={20} />
          <ParallxWordmark height={10} className="apphead-word" />
        </a>
      </div>
    </header>
  );
}
