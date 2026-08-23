import type { CardRevision } from "@/lib/types";

/**
 * One tick per filing that touched this card — filled when something moved,
 * hollow when the filing restated it without changing anything.
 *
 * This is the difference between a memory card and a stat tile. The hollow
 * ticks are deliberately shown rather than hidden: a 10-Q that repeated a
 * number is part of that number's story.
 */
export function RevisionRail({
  revisions,
  revisionCount,
  materialCount,
}: {
  revisions?: CardRevision[];
  revisionCount: number;
  materialCount: number;
}) {
  const ticks =
    revisions?.map((r) => r.is_material) ??
    Array.from({ length: revisionCount }, (_, i) => i < materialCount);

  return (
    <div className="rail">
      <div className="ticks">
        {ticks.map((material, i) => (
          <i
            key={i}
            className={material ? "tick tick-material" : "tick"}
            title={material ? "material change" : "restated without change"}
          />
        ))}
      </div>
      <span className="rail-n">
        {revisionCount} revisions · {materialCount} material
      </span>
    </div>
  );
}
