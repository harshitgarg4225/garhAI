/**
 * useSetbackContext — the buildable envelope, for the tools that must know it.
 *
 * `useToolController` takes an optional `SetbackContext`. Only the balcony tool
 * reads it today (it raises a non-blocking chip when a projection crosses the
 * building line), but it is the right shape for anything else that needs to
 * know where you may and may not build.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ONE SETBACK TABLE, NOT TWO
 * ════════════════════════════════════════════════════════════════════════════
 * The numbers come from `features/plot/rules` — the same `resolveRegValues`
 * the plot editor's regulatory panel shows, against the same rule pack. The
 * playbook is explicit that the tools must not become a second, quietly
 * divergent implementation of the setback table, and the way that stays true is
 * that this hook computes NOTHING: it classifies edges, asks the resolver, and
 * offsets the polygon with `offsetPolygon` from the model core.
 *
 * `null` is returned honestly whenever any input is missing — no plot, no pack,
 * the pack does not resolve a setback for this plot's band, or the offset
 * self-intersects (a deep setback on a narrow plot has no buildable area). The
 * balcony tool then simply raises no envelope chip, which is better than
 * inventing a building line the bye-law never drew.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * INTEGER MILLIMETRES
 * ════════════════════════════════════════════════════════════════════════════
 * Every distance here is an integer mm straight out of the rule pack, and
 * `offsetPolygon` returns integer points. The envelope is only ever read, never
 * dispatched, but keeping it integral means it can be compared to model
 * geometry without an epsilon.
 */

import { useMemo } from 'react';

import { offsetPolygon, polygonAreaMm2, type JsonObject, type Polygon } from '@garh/model';

import {
  buildRegFacts,
  frontEdgeIndex,
  resolveRegValues,
  useRulepack,
  type RulepackDoc,
} from '../../../features/plot';
import { useModelStore } from '../../../stores/model';
import type { SetbackContext } from '../../../features/canvas/tools';

/**
 * Which setback applies to each edge of the boundary.
 *
 * The front is the edge with the widest road (the same rule `buildRegFacts`
 * uses to pick the road width that binds). The rear is the edge "opposite" it —
 * for the rect/L/T plots the MVP supports, that is `front + half the edge
 * count`, which is exact for a rectangle and a defensible approximation for the
 * others. Everything else is a side.
 *
 * Reported rather than hidden: when there is no road at all there is no front,
 * so every edge gets the side value and the caller sees the wider envelope that
 * implies — an honest "we do not know which way this plot faces".
 */
function edgeDistances(
  edgeCount: number,
  frontIndex: number | null,
  front: number,
  rear: number,
  side: number,
): number[] {
  const out = new Array<number>(edgeCount).fill(side);
  if (frontIndex === null) return out;
  out[frontIndex % edgeCount] = front;
  const rearIndex = (frontIndex + Math.floor(edgeCount / 2)) % edgeCount;
  if (rearIndex !== frontIndex % edgeCount) out[rearIndex] = rear;
  return out;
}

function envelopeFor(
  boundary: Polygon,
  pack: RulepackDoc,
  roads: Parameters<typeof buildRegFacts>[0]['roads'],
  overrides: JsonObject,
): { envelope: Polygon | null; cite: string | null } {
  if (boundary.length < 3) return { envelope: null, cite: null };

  const facts = buildRegFacts({ boundaryAreaMm2: polygonAreaMm2(boundary), roads });
  const resolved = resolveRegValues(pack, facts, overrides);

  const front = resolved.values.setbackFrontMm;
  const rear = resolved.values.setbackRearMm;
  const side = resolved.values.setbackSideMm;
  // All three or nothing: a "buildable envelope" derived from one of the three
  // is not an envelope, it is a guess wearing an envelope's name.
  if (front === undefined || rear === undefined || side === undefined) {
    return { envelope: null, cite: null };
  }

  const distances = edgeDistances(
    boundary.length,
    frontEdgeIndex(roads),
    front.value,
    rear.value,
    side.value,
  );

  const envelope = offsetPolygon(boundary, distances);
  return {
    envelope: envelope === null || envelope.length < 3 ? null : envelope,
    // The front setback is the one an architect argues about, so its citation
    // is the one worth putting on the chip.
    cite: front.cite ?? front.citationsBase,
  };
}

/**
 * The buildable envelope for the current plot, or `null`.
 *
 * Also returns the maximum permitted projection beyond the building line.
 * MVP honesty: the seeded packs express projections as their own rules rather
 * than as a resolvable value key, so this is always `null` today and the
 * balcony tool falls back to its own NBC default. Wiring it is a one-line
 * change here once `REG_VALUE_KEYS` grows a `projectionMaxMm`.
 */
export function useSetbackContext(): SetbackContext | null {
  const boundary = useModelStore((s) => s.doc.plot.boundary);
  const roads = useModelStore((s) => s.doc.plot.roads);
  const regProfile = useModelStore((s) => s.doc.plot.regProfile);

  const pack = useRulepack(regProfile.cityPack);

  return useMemo<SetbackContext | null>(() => {
    if (pack.state !== 'ready') return null;
    const { envelope, cite } = envelopeFor(boundary, pack.data, roads, regProfile.overrides);
    if (envelope === null) return null;
    return { envelope, maxProjectionMm: null, cite };
  }, [boundary, roads, regProfile.overrides, pack]);
}
