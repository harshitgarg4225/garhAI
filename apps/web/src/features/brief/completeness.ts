/**
 * completeness.ts — the brief completeness meter (§F2, golden rule 8).
 *
 * A weighted checklist, not a mystery score. Every item has a fixed integer
 * weight (they sum to exactly 100), a plain-language label and a hint that
 * says why answering it improves the generated options. The meter shows the
 * score AND the highest-weight missing items, so the empty state teaches what
 * to do next instead of just admonishing.
 *
 * Deliberately simple and explainable — same principle as the Python parser's
 * `BriefParseResult.completeness()`: an architect should be able to see why the
 * meter moved when they answer a question. The two computations are cousins,
 * not mirrors: the server one scores *what the client stated in text*, this one
 * scores *what the brief now contains*, which is the number the dashboard chip
 * and `brief.update.completeness` carry.
 *
 * Pure and deterministic: (data) in, integer 0–100 out. No store access, no
 * Date, no random — safe to call inside a dispatch and in tests.
 */

import type { JsonObject } from '@garh/model';

import { readBriefData, roomCount, type BriefData } from './types';

export interface CompletenessItem {
  /** Stable id — the meter uses it to jump focus to the owning form section. */
  readonly id: string;
  /** What is missing, imperative and plain: "Add at least one bedroom". */
  readonly label: string;
  /** Why answering helps — one sentence, no jargon. */
  readonly hint: string;
  readonly weight: number;
}

export interface CompletenessResult {
  /** Integer 0–100 — exactly what `brief.update.completeness` stores. */
  readonly score: number;
  /** Unanswered items, highest weight first (stable within equal weights). */
  readonly missing: readonly CompletenessItem[];
  /** Ids of the answered items, in checklist order (for tests and debugging). */
  readonly answered: readonly string[];
}

interface ChecklistEntry extends CompletenessItem {
  readonly isAnswered: (data: BriefData) => boolean;
}

function hasBedroom(data: BriefData): boolean {
  return roomCount(data.rooms, 'bedroom_master') + roomCount(data.rooms, 'bedroom') > 0;
}

function hasBath(data: BriefData): boolean {
  const bathRooms =
    roomCount(data.rooms, 'bath') + roomCount(data.rooms, 'wc') + roomCount(data.rooms, 'bath_wc');
  if (bathRooms > 0) return true;
  return (data.rooms ?? []).some(
    (r) => (r.type === 'bedroom' || r.type === 'bedroom_master') && r.bath != null,
  );
}

/**
 * THE checklist. Weights are integers summing to 100 — asserted by the spec,
 * so a re-weighting that forgets the invariant fails in CI, not in a demo.
 */
export const COMPLETENESS_CHECKLIST: readonly ChecklistEntry[] = [
  {
    id: 'bedrooms',
    label: 'Add at least one bedroom',
    hint: 'Bedroom count drives the whole programme — the solver sizes everything else around it.',
    weight: 20,
    isAnswered: hasBedroom,
  },
  {
    id: 'baths',
    label: 'Say how baths work',
    hint: 'Attached or common per bedroom decides the plumbing stacks and the wet-area zoning.',
    weight: 10,
    isAnswered: hasBath,
  },
  {
    id: 'kitchen',
    label: 'Add the kitchen',
    hint: 'Every plan needs one — and its Vastu zone is the hardest to move later.',
    weight: 10,
    isAnswered: (d) => roomCount(d.rooms, 'kitchen') > 0,
  },
  {
    id: 'kitchen-type',
    label: 'Pick a kitchen type',
    hint: 'Open, semi-open or closed changes the living-area layout completely.',
    weight: 5,
    isAnswered: (d) => d.kitchenType !== undefined,
  },
  {
    id: 'living-dining',
    label: 'Combined or separate living/dining',
    hint: 'This is the biggest single area decision on the ground floor.',
    weight: 10,
    isAnswered: (d) =>
      d.livingDining !== undefined ||
      roomCount(d.rooms, 'living_dining') > 0 ||
      roomCount(d.rooms, 'living') > 0,
  },
  {
    id: 'storeys',
    label: 'How many floors',
    hint: 'G, G+1 or G+2 sets the stair, the structure and the FAR headroom.',
    weight: 10,
    isAnswered: (d) => d.storeys !== undefined && d.storeys >= 1,
  },
  {
    id: 'budget',
    label: 'Give a budget',
    hint: 'The budget and the ₹/sq ft rate together set the built-up area target.',
    weight: 10,
    isAnswered: (d) => d.budgetInr !== undefined && d.budgetInr > 0,
  },
  {
    id: 'style',
    label: 'Choose a style',
    hint: 'Contemporary or Modern Minimal — it drives the facade options in 3D.',
    weight: 5,
    isAnswered: (d) => d.styleKitId != null,
  },
  {
    id: 'vastu',
    label: 'Decide on Vastu',
    hint: 'Off, advisory or strict — deciding it now avoids re-generating plans later.',
    weight: 5,
    isAnswered: (d) => d.vastuDecided === true,
  },
  {
    id: 'parking',
    label: 'Parking spaces',
    hint: 'Municipal packs check parking count against the dwelling size.',
    weight: 5,
    isAnswered: (d) => d.parkingCount !== undefined,
  },
  {
    id: 'family',
    label: 'Family size',
    hint: 'Helps size the dining, wardrobes and water storage sensibly.',
    weight: 5,
    isAnswered: (d) => d.familySize !== undefined && d.familySize > 0,
  },
  {
    id: 'terrace',
    label: 'Terrace access',
    hint: 'Terrace access adds a mumty and changes the stair run.',
    weight: 3,
    isAnswered: (d) => d.terraceAccess !== undefined,
  },
  {
    id: 'expansion',
    label: 'Future expansion',
    hint: 'Planning a future floor changes column sizing and stair placement today.',
    weight: 2,
    isAnswered: (d) => d.futureExpansion !== undefined,
  },
];

/** Sum of all checklist weights — must be exactly 100 (asserted in the spec). */
export const COMPLETENESS_TOTAL_WEIGHT: number = COMPLETENESS_CHECKLIST.reduce(
  (sum, item) => sum + item.weight,
  0,
);

/**
 * Compute the completeness of a brief. Accepts the raw `brief.data` object;
 * the tolerant reader means garbage fields count as unanswered, never crash.
 */
export function computeCompleteness(data: JsonObject): CompletenessResult {
  const brief = readBriefData(data);
  const answered: string[] = [];
  const missing: CompletenessItem[] = [];
  let score = 0;

  for (const entry of COMPLETENESS_CHECKLIST) {
    if (entry.isAnswered(brief)) {
      score += entry.weight;
      answered.push(entry.id);
    } else {
      missing.push({ id: entry.id, label: entry.label, hint: entry.hint, weight: entry.weight });
    }
  }

  // Highest impact first; equal weights keep checklist order (stable sort).
  missing.sort((a, b) => b.weight - a.weight);
  return { score, missing, answered };
}
