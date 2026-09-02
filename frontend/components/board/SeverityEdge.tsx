import { edgeWidth, kindColor } from "@/lib/format";
import type { NeedKind } from "@/lib/types";

/**
 * The signature element.
 *
 * Thickness encodes urgency (2px to 8px), colour encodes the kind of need. Not
 * a badge, not a pill, not a coloured dot -- the point is that a column of these
 * reads as a bar chart you can scan without reading a single number, which is
 * what triage under stress actually needs.
 */
export function SeverityEdge({
  urgency,
  kind,
  dimmed = false,
}: {
  urgency: number | null;
  kind: NeedKind | null;
  dimmed?: boolean;
}) {
  const width = edgeWidth(urgency);
  return (
    <span
      aria-hidden="true"
      className="block h-full shrink-0"
      style={{
        width: `${width}px`,
        background: kindColor(kind),
        opacity: dimmed ? 0.45 : 1,
      }}
    />
  );
}
