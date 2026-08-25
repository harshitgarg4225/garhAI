/**
 * dirty.ts — which rebuild groups an edit actually touched.
 *
 * THE DESIGN: dirty tracking is SIGNATURE-BASED, not op-classification-based.
 * Each group (one per storey, plus `roof`) gets a cheap deterministic string
 * built from exactly the model slice its geometry reads. After any change —
 * an op fold, an undo, a server rebase, a whole-document reload — the scene
 * recomputes signatures (string building over already-sorted arrays; no
 * geometry) and rebuilds only groups whose signature moved.
 *
 * Why not classify ops? A table of "op type → dirty storeys" drifts the day
 * fold.ts grows a new derived effect, and it cannot see effects that arrive
 * WITHOUT an op (rebase, version restore, solver apply expanding to many
 * ops). The signature reads the same folded document the geometry reads, so
 * it cannot disagree with it. The op-level behaviour the spec asks for falls
 * out and is pinned per-op-type in `dirty.test.ts`:
 *
 *   wall/opening/stair/column/balcony ops   → host storey only (plus the
 *     storey above when fold's derived slab there changes — stair wells —
 *     and the roof when the edit is on the top storey's envelope)
 *   storey.set_height / storey.add/remove   → the storey, everything above
 *     (FFLs shift), and the roof
 *   levels.set plinth                       → every storey + roof
 *   levels.set parapetMm                    → roof only
 *   facade.apply_kit / facade.edit_component→ NOTHING (§8 isolation)
 *   material.assign, global or storey scope → NOTHING (colour is resolved at
 *     render time); element-scoped          → the host group only (bucket
 *     membership changes)
 *   room.assign / room.set_target / furniture.set / plot.* / brief.* /
 *   annotation.set                          → NOTHING
 *
 * CROSS-STOREY EDGES, stated so nobody re-derives them:
 *  - a wall's top depends on the storey ABOVE's slab thickness → included.
 *  - a storey's slab cutouts come from the stairs BELOW (fold derives this;
 *    the slab slice is in the signature, so it propagates automatically).
 *  - the roof reads the TOP storey's envelope, stairs (mumty) and shaft
 *    rooms (OHT), plus `levels.parapetMm` → all included in `roof`'s
 *    signature.
 *  - balconies belong to exactly one storey's group; an edit dirties that
 *    group alone, which is sufficient because no other group draws them.
 */

import type { HouseModel, MaterialAssignment, Polygon } from '@garh/model';

import { ROOF_GROUP_KEY, storeyGroupKey } from './solids';

// ---------------------------------------------------------------------------
// Serialisation helpers (deterministic, allocation-light)
// ---------------------------------------------------------------------------

function ringSig(polygon: Polygon): string {
  let out = '';
  for (const p of polygon) out += `${String(p.x)},${String(p.y)};`;
  return out;
}

/** Element-scoped material assignments, keyed by the element they target. */
function elementAssignments(materials: readonly MaterialAssignment[]): Map<string, string> {
  const out = new Map<string, string>();
  for (const m of materials) {
    if (m.target.elementId !== null) out.set(m.target.elementId, `${m.id}:${m.materialId}`);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Per-group signatures
// ---------------------------------------------------------------------------

/**
 * Signature of one storey's rebuild group. Includes everything
 * `storeySolids()` reads and nothing else — rooms are deliberately absent
 * (the slab's room pick resolves at render time against live rooms, so
 * `room.assign` must not trigger a geometry rebuild), and so are furniture
 * and the facade sub-model.
 */
export function storeySignature(house: HouseModel, storeyId: string): string {
  const index = house.storeys.findIndex((s) => s.id === storeyId);
  const storey = house.storeys[index];
  if (index < 0 || storey === undefined) return 'missing';

  const above = house.storeys[index + 1];
  const elementScoped = elementAssignments(house.materials);
  const parts: string[] = [
    `i:${String(index)}`,
    `h:${String(storey.heightMm)}`,
    `ffl:${String(house.levels.fflPerStoreyMm[index] ?? -1)}`,
    `fflAbove:${String(house.levels.fflPerStoreyMm[index + 1] ?? -1)}`,
    `slab:${String(storey.level.slabThicknessMm)}`,
    `slabAbove:${String(above?.level.slabThicknessMm ?? -1)}`,
    `plinth:${index === 0 ? String(house.levels.plinthMm) : '-'}`,
  ];

  const wallIds = new Set<string>();
  for (const w of house.walls) {
    if (w.storeyId !== storeyId) continue;
    wallIds.add(w.id);
    parts.push(
      `W:${w.id}:${String(w.a.x)},${String(w.a.y)}:${String(w.b.x)},${String(w.b.y)}:${String(w.thicknessMm)}:${w.kind}`,
    );
  }
  for (const o of house.openings) {
    if (!wallIds.has(o.wallId)) continue;
    parts.push(
      `O:${o.id}:${o.wallId}:${o.kind}:${String(o.widthMm)}:${String(o.heightMm)}:${String(o.sillMm)}:${String(o.offsetMm)}`,
    );
  }
  for (const s of house.slabs) {
    if (s.storeyId !== storeyId) continue;
    parts.push(
      `SL:${s.id}:${s.kind}:${String(s.thicknessMm)}:${ringSig(s.polygon)}:${s.cutouts.map(ringSig).join('#')}`,
    );
  }
  const elementIds: string[] = [...wallIds];
  for (const s of house.stairs) {
    if (s.storeyId !== storeyId) continue;
    elementIds.push(s.id);
    parts.push(
      `ST:${s.id}:${s.kind}:${String(s.origin.x)},${String(s.origin.y)}:${s.direction}:${String(s.riserMm)}:${String(s.treadMm)}:${String(s.widthMm)}:${String(s.risersCount)}:${s.landing === null ? '-' : `${String(s.landing.widthMm)}x${String(s.landing.depthMm)}`}`,
    );
  }
  for (const b of house.balconies) {
    if (b.storeyId !== storeyId) continue;
    elementIds.push(b.id);
    parts.push(
      `B:${b.id}:${b.railingKind}:${String(b.railingHeightMm)}:${String(b.slabThicknessMm)}:${ringSig(b.polygon)}`,
    );
  }
  for (const c of house.columns) {
    if (c.storeyId !== storeyId) continue;
    elementIds.push(c.id);
    parts.push(
      `C:${c.id}:${String(c.pt.x)},${String(c.pt.y)}:${String(c.sizeMm.xMm)}x${String(c.sizeMm.yMm)}`,
    );
  }
  for (const o of house.openings) if (wallIds.has(o.wallId)) elementIds.push(o.id);
  for (const s of house.slabs) if (s.storeyId === storeyId) elementIds.push(s.id);

  // Element-scoped material assignments change bucket membership, so they are
  // part of the build input. Global and storey-scoped assignments are looked
  // up at render time and deliberately NOT part of the signature.
  for (const id of elementIds.sort()) {
    const scoped = elementScoped.get(id);
    if (scoped !== undefined) parts.push(`EA:${id}:${scoped}`);
  }

  return parts.join('|');
}

/** Signature of the roof group: everything `roofSolids()` reads. */
export function roofSignature(house: HouseModel): string {
  const top = house.storeys[house.storeys.length - 1];
  if (top === undefined) return 'empty';

  const parts: string[] = [
    `top:${top.id}`,
    `h:${String(top.heightMm)}`,
    `ffl:${String(house.levels.fflPerStoreyMm[house.storeys.length - 1] ?? -1)}`,
    `slab:${String(top.level.slabThicknessMm)}`,
    `parapet:${String(house.levels.parapetMm)}`,
  ];
  for (const s of house.slabs) {
    if (s.storeyId !== top.id || s.kind !== 'floor') continue;
    parts.push(`SL:${ringSig(s.polygon)}`);
  }
  for (const s of house.stairs) {
    if (s.storeyId !== top.id) continue;
    parts.push(
      `ST:${s.id}:${s.kind}:${String(s.origin.x)},${String(s.origin.y)}:${s.direction}:${String(s.riserMm)}:${String(s.treadMm)}:${String(s.widthMm)}:${String(s.risersCount)}:${s.landing === null ? '-' : `${String(s.landing.widthMm)}x${String(s.landing.depthMm)}`}`,
    );
  }
  for (const r of house.rooms) {
    if (r.storeyId !== top.id || r.type !== 'shaft') continue;
    parts.push(`SH:${r.id}:${ringSig(r.polygon)}`);
  }
  return parts.join('|');
}

/** Signatures of every group in the document, in draw order. */
export function groupSignatures(house: HouseModel): Map<string, string> {
  const out = new Map<string, string>();
  for (const storey of house.storeys) {
    out.set(storeyGroupKey(storey.id), storeySignature(house, storey.id));
  }
  if (house.storeys.length > 0) out.set(ROOF_GROUP_KEY, roofSignature(house));
  return out;
}

// ---------------------------------------------------------------------------
// The rebuild plan
// ---------------------------------------------------------------------------

export interface RebuildPlan {
  /** Groups whose signature changed or that are new — re-synthesise these. */
  readonly rebuild: readonly string[];
  /** Groups whose signature is unchanged — keep their cached meshes. */
  readonly keep: readonly string[];
  /** Cached groups that no longer exist (storey removed) — dispose these. */
  readonly drop: readonly string[];
}

/**
 * Diff the previous signatures against the next document. Pure — this is the
 * function `dirty.test.ts` pins per op type, and the scene's cache loop is a
 * direct transcription of its output.
 */
export function planRebuild(
  prev: ReadonlyMap<string, string> | null,
  next: ReadonlyMap<string, string>,
): RebuildPlan {
  const rebuild: string[] = [];
  const keep: string[] = [];
  const drop: string[] = [];

  for (const [key, sig] of next) {
    const before = prev?.get(key);
    if (before === sig) keep.push(key);
    else rebuild.push(key);
  }
  if (prev !== null) {
    for (const key of prev.keys()) {
      if (!next.has(key)) drop.push(key);
    }
  }
  return { rebuild, keep, drop };
}
