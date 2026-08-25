/**
 * KitThumbnail.tsx — a kit card's preview, drawn from the generator's own
 * geometry (`thumbnail.ts`), as inline SVG.
 *
 * There is no image file behind this and there must never be one: a static
 * preview drifts from the generator the first time a kit constant changes
 * (inherited fact 4 gates binary assets anyway). The SVG re-renders when the
 * kit, seed, colorway or the user's own frontage changes — the card IS the
 * generator's output, at thumbnail scale.
 */

import { useMemo } from 'react';

import type { HouseModel } from '@garh/model';

import { kitThumbnailSpec } from './thumbnail';
import type { FacadeKitDef } from './types';

export interface KitThumbnailProps {
  readonly kit: FacadeKitDef;
  readonly seed: number;
  readonly colorwayId: string | null;
  /** The user's model; the sample house is used when it has no frontage. */
  readonly house: HouseModel | null;
  readonly className?: string | undefined;
}

/** Sky behind the elevation. One hex, both themes — it reads as paper. */
const BACKDROP_HEX = '#E7ECF0';
/** Ground line under the elevation. */
const GROUND_HEX = '#C9CDC6';

export function KitThumbnail({
  kit,
  seed,
  colorwayId,
  house,
  className,
}: KitThumbnailProps): JSX.Element | null {
  const spec = useMemo(
    () => kitThumbnailSpec(house, kit, seed, colorwayId),
    [house, kit, seed, colorwayId],
  );
  if (spec === null) return null;

  // Margins so the parapet cap and porch do not touch the card edge.
  const padX = Math.round(spec.widthMm * 0.06);
  const padTop = Math.round(spec.heightMm * 0.08);
  const groundMm = Math.max(120, Math.round(spec.heightMm * 0.03));
  const viewW = spec.widthMm + 2 * padX;
  const viewH = spec.heightMm + padTop + groundMm;

  return (
    <svg
      viewBox={`0 0 ${String(viewW)} ${String(viewH)}`}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label={`${kit.name} facade preview, seed ${String(seed)}`}
      className={className}
    >
      <rect x={0} y={0} width={viewW} height={viewH} fill={BACKDROP_HEX} />
      <rect x={0} y={viewH - groundMm} width={viewW} height={groundMm} fill={GROUND_HEX} />
      {spec.rects.map((r, i) => (
        <rect
          // Order is deterministic (see elevationSpec) so the index is a
          // stable key for what is a read-only picture.
          key={i}
          x={padX + r.x}
          // Elevation y grows up; SVG y grows down.
          y={padTop + (spec.heightMm - r.y - r.h)}
          width={Math.max(r.w, 8)}
          height={Math.max(r.h, 8)}
          fill={r.fill}
        />
      ))}
    </svg>
  );
}
