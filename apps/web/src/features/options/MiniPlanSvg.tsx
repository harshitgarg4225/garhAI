/**
 * MiniPlanSvg — an option's plan drawn as inline SVG, straight from the
 * option's own JSON (walls from its op expansion, labels from placements).
 *
 * SVG rather than the shared R3F canvas for the same reason the plot editor
 * chose it (DECISIONS.md 2026-08-05): a static, text-heavy, low-element
 * thumbnail wants crisp vector text and zero WebGL contexts — a page of eight
 * option cards must not open eight GL contexts. The Phase-4 editor canvas is
 * untouched by this choice.
 *
 * The viewBox is in millimetres (see planGeometry.planViewBox), so this
 * component contains no scaling arithmetic at all — that math lives in the
 * pure module where it is unit-tested.
 */

import { cn } from '@garh/ui';

import {
  onStorey,
  planViewBox,
  unionBounds,
  boundsOfPolygon,
  type MiniPlanGeometry,
} from './planGeometry';
import type { PtMm } from './types';

export interface MiniPlanSvgProps {
  readonly geometry: MiniPlanGeometry;
  /** Which floor to draw. Defaults to the lowest floor with geometry. */
  readonly storeyIndex?: number | undefined;
  /** Plot/envelope outline drawn faintly under the walls, when available. */
  readonly outline?: readonly PtMm[] | undefined;
  /** Show room labels (off for the small theater silhouettes). */
  readonly showLabels?: boolean | undefined;
  /** Accessible name: "Option 1 floor plan". */
  readonly label: string;
  readonly className?: string | undefined;
}

export function MiniPlanSvg({
  geometry,
  storeyIndex,
  outline,
  showLabels = true,
  label,
  className,
}: MiniPlanSvgProps): JSX.Element {
  const floor = storeyIndex ?? geometry.storeyIndices[0] ?? 0;
  const scoped = onStorey(geometry, floor);

  const outlineBounds = outline ? boundsOfPolygon(outline) : null;
  const bounds = unionBounds(scoped.bounds, outlineBounds);

  if (bounds === null) {
    // No geometry to draw — an honest blank tile, not a fabricated sketch.
    return (
      <div
        role="img"
        aria-label={`${label} — no drawable geometry`}
        className={cn(
          'flex aspect-square items-center justify-center rounded-md bg-surface-sunken text-2xs text-ink-subtle',
          className,
        )}
      >
        No preview
      </div>
    );
  }

  const view = planViewBox(bounds);

  return (
    <svg
      viewBox={view.viewBox}
      role="img"
      aria-label={label}
      className={cn('block h-auto w-full rounded-md bg-surface-sunken', className)}
      preserveAspectRatio="xMidYMid meet"
    >
      {outline && outline.length >= 3 ? (
        <polygon
          points={outline.map((p) => `${view.toView(p).x},${view.toView(p).y}`).join(' ')}
          fill="none"
          stroke="currentColor"
          strokeWidth={view.strokeFor(0) / 2}
          strokeDasharray={`${view.strokeFor(0) * 2} ${view.strokeFor(0) * 2}`}
          className="text-line"
        />
      ) : null}

      {scoped.walls.map((wall, i) => {
        const a = view.toView(wall.a);
        const b = view.toView(wall.b);
        return (
          <line
            key={i}
            x1={a.x}
            y1={a.y}
            x2={b.x}
            y2={b.y}
            stroke="currentColor"
            strokeWidth={view.strokeFor(wall.thicknessMm)}
            strokeLinecap="square"
            className={wall.kind === 'external' ? 'text-ink' : 'text-ink-muted'}
          />
        );
      })}

      {showLabels
        ? scoped.labels.map((room, i) => {
            const at = view.toView({ x: room.x, y: room.y });
            return (
              <text
                key={i}
                x={at.x}
                y={at.y}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={view.labelFontMm}
                className="fill-current text-ink-subtle"
              >
                {room.label}
              </text>
            );
          })
        : null}
    </svg>
  );
}
