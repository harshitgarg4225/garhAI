/**
 * Compliance auto-fix — turning a pack's `autofix` hint into a real op group (§15:
 * "'Fix it' applies the suggested op diff where computable").
 *
 * The rules engine never applies anything; a pack row carries `autofix: {opType,
 * strategy}` and the engine reports `fixAvailable` when the pack says the strategy
 * is computable. That flag is the PACK's claim. Whether THIS client can build the
 * op group is decided here, per strategy, and the button appears only when both
 * agree. The failure mode this guards against is the dead button: a chip that
 * says "Fix it", is pressed, and does nothing — which teaches an architect that
 * the whole strip is decorative.
 *
 * Every plan is one op group, dispatched through the model store as ONE undo
 * entry. The strip then re-checks (the store's `baseIdx` advances when the server
 * confirms the group) and the chip clears on its own if the fix was sufficient.
 *
 * Strategies deliberately NOT computed here, and why:
 *
 *   - `grow-room-to-limit`, `widen-room-to-limit`, `move-building-line`
 *     (`wall.move`): which wall to move, by how much, without breaking the room
 *     next door, is a design decision. A guess that shifts a shared wall silently
 *     shrinks the neighbour and may fail ITS area rule — trading one red chip for
 *     another, invisibly. The hint says what to do; the architect does it.
 *   - `enlarge-openings-to-ratio` (`opening.resize` / `opening.add`): which window
 *     grows, or where a new one goes, depends on the facade and the wall it sits
 *     in; resizing blindly can push an opening past its wall. Same reasoning.
 *   - `declare-rwh` (`plot.set_reg_profile`): the "fix" would be to tick a
 *     declaration that rainwater harvesting is provided. A declaration is not a
 *     design; flipping it from a chip would make a warning disappear without
 *     anything being drawn. That belongs on the Brief, stated on purpose.
 *   - `adjust-stair-parameters` for `headroom_min`: headroom is the storey's
 *     clear height, not a stair parameter; there is nothing on the stair to edit.
 *
 * Everything the engine reports comes through the issue VM: `checkType` picks
 * the measurement, `instances[]` give the failing element and its own limit
 * (a value override can move the limit per instance), and `elementIds` are the
 * offenders. Ids are validated against the document — an element deleted since
 * the last re-check yields `null`, never an op the model will reject.
 */

import { isIdOf, roundHalfAwayFromZero } from '@garh/model';
import type { Id, Op, ProjectDoc, Stair, Storey } from '@garh/model';
import type { ComplianceInstanceVM, ComplianceIssueVM, ComplianceValueVM } from '../../components';

/** A computed fix: the ops, an undo-toast label, and a sentence for the toast. */
export interface AutofixPlan {
  readonly ops: readonly Op[];
  /** Undo-toast copy: "Storey height raised". Sentence case, no trailing period. */
  readonly label: string;
  /** What will change, for the toast: "Ground floor raised 2,750 → 2,900 mm". */
  readonly summary: string;
}

/**
 * `(strategy, checkType)` pairs this client knows how to compute. `checkType`
 * matters because one strategy serves several checks (`adjust-stair-parameters`
 * covers riser, tread, width AND headroom) and not every one is computable.
 */
const COMPUTABLE: ReadonlySet<string> = new Set([
  'raise-storey-height:ceiling_height_min',
  'resize-opening-to-limit:opening_width_min',
  'adjust-stair-parameters:stair_riser_max',
  'adjust-stair-parameters:stair_tread_min',
  'adjust-stair-parameters:stair_width_min',
  'trim-projection:projection_max',
]);

/**
 * Can this client build an op group for the pack's hint? Pure; no document
 * needed. The mapping layer ANDs this with the engine's `fixAvailable` so the
 * VM's flag means "a button here will do something".
 */
export function hasClientAutofix(
  autofix: { readonly strategy: string } | null | undefined,
  checkType: string | null | undefined,
): boolean {
  if (autofix === null || autofix === undefined) return false;
  if (checkType === null || checkType === undefined) return false;
  return COMPUTABLE.has(`${autofix.strategy}:${checkType}`);
}

/**
 * Build the op group for an issue against the CURRENT document, or `null` when
 * nothing honest can be built (unknown strategy, element gone, limit not a
 * number). Never throws: the caller turns `null` into a toast, not a crash.
 */
export function computeAutofix(issue: ComplianceIssueVM, doc: ProjectDoc): AutofixPlan | null {
  if (!hasClientAutofix(issue.autofix, issue.checkType)) return null;
  const targets = failingTargets(issue);
  if (targets.length === 0) return null;

  switch (issue.autofix?.strategy) {
    case 'raise-storey-height':
      return raiseStoreyHeight(targets, doc);
    case 'resize-opening-to-limit':
      return resizeOpenings(targets, doc);
    case 'adjust-stair-parameters':
      return adjustStairs(issue.checkType ?? '', targets, doc);
    case 'trim-projection':
      return trimProjections(targets, doc);
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Targets: which element, what it measured, what it must reach
// ---------------------------------------------------------------------------

interface FixTarget {
  readonly elementId: string;
  readonly actual: number | null;
  readonly limit: number;
}

function asNumber(value: ComplianceValueVM | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/**
 * The failing elements with their own limits. Instances are preferred — they
 * carry the per-element limit after any value override — and `elementIds` is
 * the fallback for a row whose instances the wire omitted, taking the row-level
 * limit. Passing instances are skipped: a fix touches only what failed.
 */
function failingTargets(issue: ComplianceIssueVM): FixTarget[] {
  const out: FixTarget[] = [];
  const seen = new Set<string>();
  const instances: readonly ComplianceInstanceVM[] = issue.instances ?? [];
  for (const inst of instances) {
    if (inst.elementId === null || inst.status === 'pass' || inst.status === 'not_applicable') {
      continue;
    }
    const limit = asNumber(inst.limit) ?? asNumber(issue.limit);
    if (limit === null) continue;
    seen.add(inst.elementId);
    out.push({ elementId: inst.elementId, actual: asNumber(inst.actual), limit });
  }
  if (out.length === 0) {
    const limit = asNumber(issue.limit);
    if (limit === null) return out;
    for (const elementId of issue.elementIds) {
      if (seen.has(elementId)) continue;
      seen.add(elementId);
      out.push({ elementId, actual: asNumber(issue.actual), limit });
    }
  }
  return out;
}

function fmtMm(mm: number): string {
  return `${mm.toLocaleString('en-IN')} mm`;
}

// ---------------------------------------------------------------------------
// Strategies
// ---------------------------------------------------------------------------

/**
 * `ceiling_height_min` is measured per ROOM (a dropped ceiling over one wet area
 * is the case it exists for), but the op that changes it is per STOREY. The room's
 * clear height is the storey height less whatever the projection deducts, so the
 * storey is raised by the room's shortfall — not set to the limit outright, which
 * would be wrong by exactly that deduction. Several rooms on one storey collapse
 * to one op with the largest shortfall.
 */
function raiseStoreyHeight(targets: readonly FixTarget[], doc: ProjectDoc): AutofixPlan | null {
  const raiseBy = new Map<Id<'storey'>, number>();
  for (const t of targets) {
    if (t.actual === null) return null;
    const shortfall = t.limit - t.actual;
    if (shortfall <= 0) continue;
    const room = doc.house.rooms.find((r) => r.id === t.elementId);
    if (room === undefined) return null;
    raiseBy.set(room.storeyId, Math.max(raiseBy.get(room.storeyId) ?? 0, shortfall));
  }
  if (raiseBy.size === 0) return null;

  const ops: Op[] = [];
  const parts: string[] = [];
  for (const [storeyId, shortfall] of raiseBy) {
    const storey: Storey | undefined = doc.house.storeys.find((s) => s.id === storeyId);
    if (storey === undefined) return null;
    const heightMm = storey.heightMm + shortfall;
    ops.push({ type: 'storey.set_height', payload: { storeyId, heightMm } });
    parts.push(`${storey.name} ${fmtMm(storey.heightMm)} → ${fmtMm(heightMm)}`);
  }
  return {
    ops,
    label: ops.length === 1 ? 'Storey height raised' : 'Storey heights raised',
    summary: parts.join('; '),
  };
}

function resizeOpenings(targets: readonly FixTarget[], doc: ProjectDoc): AutofixPlan | null {
  const ops: Op[] = [];
  const parts: string[] = [];
  for (const t of targets) {
    if (!isIdOf('opening', t.elementId)) return null;
    const opening = doc.house.openings.find((o) => o.id === t.elementId);
    if (opening === undefined) return null;
    if (opening.widthMm >= t.limit) continue;
    ops.push({
      type: 'opening.resize',
      payload: { openingId: opening.id, widthMm: t.limit },
    });
    parts.push(`${opening.kind} ${fmtMm(opening.widthMm)} → ${fmtMm(t.limit)}`);
  }
  if (ops.length === 0) return null;
  return {
    ops,
    label: ops.length === 1 ? 'Opening widened' : 'Openings widened',
    summary: parts.join('; '),
  };
}

const STAIR_FIELD: Readonly<Record<string, 'riserMm' | 'treadMm' | 'widthMm'>> = {
  stair_riser_max: 'riserMm',
  stair_tread_min: 'treadMm',
  stair_width_min: 'widthMm',
};

const STAIR_LABEL: Readonly<Record<'riserMm' | 'treadMm' | 'widthMm', string>> = {
  riserMm: 'riser',
  treadMm: 'tread',
  widthMm: 'width',
};

function adjustStairs(
  checkType: string,
  targets: readonly FixTarget[],
  doc: ProjectDoc,
): AutofixPlan | null {
  const field = STAIR_FIELD[checkType];
  if (field === undefined) return null;
  const ops: Op[] = [];
  const parts: string[] = [];
  for (const t of targets) {
    if (!isIdOf('stair', t.elementId)) return null;
    const stair: Stair | undefined = doc.house.stairs.find((s) => s.id === t.elementId);
    if (stair === undefined) return null;
    const current = stair[field];
    // A max rule (riser) is met by coming DOWN to the limit; a min rule by going up.
    const satisfied = field === 'riserMm' ? current <= t.limit : current >= t.limit;
    if (satisfied) continue;
    if (field === 'riserMm') {
      // The model holds `risersCount × riserMm` to the storey height (±10 mm), so a
      // lower riser means MORE risers: the flight is re-counted, then the riser is
      // whatever divides the storey evenly. A riser that cannot land within the
      // tolerance yields no plan rather than an op the model would reject.
      const storey = doc.house.storeys.find((s) => s.id === stair.storeyId);
      if (storey === undefined) return null;
      const risersCount = Math.ceil(storey.heightMm / t.limit);
      const riserMm = roundHalfAwayFromZero(storey.heightMm / risersCount);
      if (riserMm > t.limit || Math.abs(risersCount * riserMm - storey.heightMm) > 10) return null;
      ops.push({
        type: 'stair.edit',
        payload: { stairId: stair.id, patch: { riserMm, risersCount } },
      });
      parts.push(
        `riser ${fmtMm(current)} → ${fmtMm(riserMm)} (${stair.risersCount} → ${risersCount} risers)`,
      );
      continue;
    }
    ops.push({ type: 'stair.edit', payload: { stairId: stair.id, patch: { [field]: t.limit } } });
    parts.push(`${STAIR_LABEL[field]} ${fmtMm(current)} → ${fmtMm(t.limit)}`);
  }
  if (ops.length === 0) return null;
  return { ops, label: `Stair ${STAIR_LABEL[field]} adjusted`, summary: parts.join('; ') };
}

function trimProjections(targets: readonly FixTarget[], doc: ProjectDoc): AutofixPlan | null {
  const ops: Op[] = [];
  const parts: string[] = [];
  for (const t of targets) {
    if (!isIdOf('balcony', t.elementId)) return null;
    const balcony = doc.house.balconies.find((b) => b.id === t.elementId);
    if (balcony === undefined) return null;
    if (balcony.projectionMm <= t.limit) continue;
    ops.push({
      type: 'balcony.set',
      payload: { action: 'edit', id: balcony.id, projectionMm: t.limit },
    });
    parts.push(`projection ${fmtMm(balcony.projectionMm)} → ${fmtMm(t.limit)}`);
  }
  if (ops.length === 0) return null;
  return {
    ops,
    label: ops.length === 1 ? 'Projection trimmed' : 'Projections trimmed',
    summary: parts.join('; '),
  };
}
