/**
 * MiniDocPlan — one storey of a ProjectDoc as a static SVG thumbnail.
 *
 * This is what the copilot's DiffPreview plugs into its `renderBefore` /
 * `renderAfter` slots. SVG rather than the shared R3F canvas for the same
 * reason the options screen chose it (DECISIONS.md 2026-08-05): a chat rail
 * can hold many diffs, and each must not cost a WebGL context.
 *
 * `highlightIds` tints the touched elements so the eye lands on what the
 * change actually does — the after-tile passes the diff's element ids.
 */

import { useMemo } from 'react';

import { cn } from '@garh/ui';
import type { ProjectDoc } from '@garh/model';

import { docPlanForStorey, docPlanViewBox } from './docPlan';

export interface MiniDocPlanProps {
  readonly doc: ProjectDoc;
  readonly storeyId: string | null;
  /**
   * The OTHER document of the pair (before ↔ after). Only its geometry bounds
   * are used, so both tiles share one frame and nothing appears to jump.
   */
  readonly frameWith?: ProjectDoc | undefined;
  /** Element ids to tint (the diff's touched elements). */
  readonly highlightIds?: readonly string[] | undefined;
  /** Accessible name: "Plan before the change". */
  readonly label: string;
  readonly className?: string | undefined;
}

export function MiniDocPlan({
  doc,
  storeyId,
  frameWith,
  highlightIds,
  label,
  className,
}: MiniDocPlanProps): JSX.Element {
  const { geometry, view } = useMemo(() => {
    const own = docPlanForStorey(doc, storeyId);
    const other = frameWith === undefined ? null : docPlanForStorey(frameWith, storeyId);
    return {
      geometry: own,
      view: docPlanViewBox(other === null ? [own] : [own, other]),
    };
  }, [doc, frameWith, storeyId]);

  const highlighted = useMemo(() => new Set(highlightIds ?? []), [highlightIds]);

  if (view === null) {
    // Nothing drawable — an honest blank, not a fabricated sketch.
    return (
      <div
        role="img"
        aria-label={`${label} — nothing to draw yet`}
        className={cn(
          'flex h-full w-full items-center justify-center text-2xs text-ink-subtle',
          className,
        )}
      >
        Nothing on this floor yet
      </div>
    );
  }

  return (
    <svg
      viewBox={view.viewBox}
      role="img"
      aria-label={label}
      className={cn('block h-full w-full', className)}
      preserveAspectRatio="xMidYMid meet"
    >
      {geometry.walls.map((wall) => {
        const a = view.toView(wall.a);
        const b = view.toView(wall.b);
        const hot = highlighted.has(wall.id);
        return (
          <line
            key={wall.id}
            x1={a.x}
            y1={a.y}
            x2={b.x}
            y2={b.y}
            stroke="currentColor"
            strokeWidth={view.strokeFor(wall.thicknessMm)}
            strokeLinecap="square"
            className={
              hot ? 'text-brand' : wall.kind === 'external' ? 'text-ink' : 'text-ink-muted'
            }
          />
        );
      })}

      {/* Openings: a cut through the wall, then a thin sill/leaf line so a
          window reads differently from a plain gap. */}
      {geometry.openings.map((opening) => {
        const a = view.toView(opening.a);
        const b = view.toView(opening.b);
        const hot = highlighted.has(opening.id);
        const cut = view.strokeFor(opening.wallThicknessMm) * 1.25;
        return (
          <g key={opening.id}>
            <line
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              stroke="currentColor"
              strokeWidth={cut}
              strokeLinecap="butt"
              className="text-surface-sunken"
            />
            <line
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              stroke="currentColor"
              strokeWidth={cut / (opening.kind === 'window' ? 4 : 8)}
              strokeLinecap="butt"
              className={hot ? 'text-brand' : 'text-ink-subtle'}
            />
          </g>
        );
      })}

      {geometry.labels.map((room) => {
        const at = view.toView({ x: room.x, y: room.y });
        return (
          <text
            key={room.id}
            x={at.x}
            y={at.y}
            textAnchor="middle"
            dominantBaseline="central"
            fontSize={view.labelFontMm}
            className={cn(
              'fill-current',
              highlighted.has(room.id) ? 'text-brand-ink' : 'text-ink-subtle',
            )}
          >
            {room.label}
          </text>
        );
      })}
    </svg>
  );
}
