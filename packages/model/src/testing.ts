/**
 * testing.ts — deterministic fixtures shared by this package's tests and by
 * `apps/web`'s store tests.
 *
 * Everything here uses FIXED ids (not random ULIDs) so that a state hash printed
 * by one test is meaningful in another, and so failures diff readably.
 *
 * This is test support, not product code: it must never be imported by a
 * runtime path. It lives in `src/` (rather than a test folder) only so that
 * other packages can import it from `@garh/model`.
 */

import type { ElementType, Id } from './ids';
import { DEFAULTS, emptyProjectDoc } from './model';
import type { ProjectDoc } from './model';
import type { Op } from './ops';
import { applyGroup } from './fold';

/**
 * A stable, readable, VALID element id: `type_01J0000000000000000000TAG`.
 * `tag` is upper-cased and stripped to the Crockford alphabet.
 */
export function fixedId<T extends ElementType>(type: T, tag: string): Id<T> {
  const clean = tag.toUpperCase().replace(/[^0-9ABCDEFGHJKMNPQRSTVWXYZ]/g, '');
  const body = `01J${'0'.repeat(26)}`.slice(0, 26 - clean.length) + clean;
  return `${type}_${body}`;
}

/** Ids used by {@link twoRoomPlanOps}. */
export const FIXTURE_IDS = {
  groundStorey: fixedId('storey', 'GF'),
  firstStorey: fixedId('storey', 'FF'),
  wallSouth: fixedId('wall', 'WS'),
  wallEast: fixedId('wall', 'WE'),
  wallNorth: fixedId('wall', 'WN'),
  wallWest: fixedId('wall', 'WW'),
  wallSpine: fixedId('wall', 'WSP'),
  doorMain: fixedId('opening', 'D1'),
  windowWest: fixedId('opening', 'W1'),
  stair: fixedId('stair', 'ST1'),
  column: fixedId('column', 'C1'),
  sofa: fixedId('furniture', 'FS1'),
  balcony: fixedId('balcony', 'B1'),
  material: fixedId('material', 'M1'),
  annotation: fixedId('annotation', 'A1'),
  sheet: fixedId('sheet', 'SH1'),
} as const;

/** The demo plot: 30 x 40 ft (9144 x 12192 mm) Bengaluru plot, north up. */
export const DEMO_PLOT_POLYGON = [
  { x: 0, y: 0 },
  { x: 9144, y: 0 },
  { x: 9144, y: 12192 },
  { x: 0, y: 12192 },
];

/** An empty document with ft-in display, the state every op log folds from. */
export function makeEmptyDoc(): ProjectDoc {
  return emptyProjectDoc('ft-in');
}

/**
 * Ops that build a ground floor with TWO rooms:
 *
 *   (0,4000) +-----------+-----------+ (6000,4000)
 *            |           |           |
 *            |  room A   |  room B   |     external walls 230mm
 *            |           |           |     spine wall     115mm
 *      (0,0) +-----------+-----------+ (6000,0)
 *                    x = 3000
 *
 * Clear areas (centreline face inset by half thickness):
 *   A = (2943-115) x (3885-115) = 2828 x 3770 = 10_661_560 mm^2
 *   B = (5885-3057) x (3885-115) = 2828 x 3770 = 10_661_560 mm^2
 */
export function twoRoomPlanOps(): Op[] {
  const s = FIXTURE_IDS.groundStorey;
  return [
    {
      type: 'plot.set_boundary',
      payload: { polygon: DEMO_PLOT_POLYGON, source: 'seed' },
    },
    { type: 'plot.set_north', payload: { deg: 0 } },
    { type: 'plot.set_road', payload: { edgeIndex: 0, widthMm: 9000, name: '9m Road' } },
    {
      type: 'storey.add',
      payload: { id: s, index: 0, name: 'Ground Floor', heightMm: DEFAULTS.storeyHeightMm },
    },
    {
      type: 'wall.add',
      payload: {
        id: FIXTURE_IDS.wallSouth,
        storeyId: s,
        a: { x: 0, y: 0 },
        b: { x: 6000, y: 0 },
        thicknessMm: 230,
        kind: 'external',
      },
    },
    {
      type: 'wall.add',
      payload: {
        id: FIXTURE_IDS.wallEast,
        storeyId: s,
        a: { x: 6000, y: 0 },
        b: { x: 6000, y: 4000 },
        thicknessMm: 230,
        kind: 'external',
      },
    },
    {
      type: 'wall.add',
      payload: {
        id: FIXTURE_IDS.wallNorth,
        storeyId: s,
        a: { x: 6000, y: 4000 },
        b: { x: 0, y: 4000 },
        thicknessMm: 230,
        kind: 'external',
      },
    },
    {
      type: 'wall.add',
      payload: {
        id: FIXTURE_IDS.wallWest,
        storeyId: s,
        a: { x: 0, y: 4000 },
        b: { x: 0, y: 0 },
        thicknessMm: 230,
        kind: 'external',
      },
    },
    {
      type: 'wall.add',
      payload: {
        id: FIXTURE_IDS.wallSpine,
        storeyId: s,
        a: { x: 3000, y: 0 },
        b: { x: 3000, y: 4000 },
        thicknessMm: 115,
        kind: 'internal',
      },
    },
  ];
}

/** The two-room plan, already folded. */
export function makeTwoRoomPlan(): ProjectDoc {
  return applyGroup(makeEmptyDoc(), twoRoomPlanOps()).model;
}

/** The two-room plan plus a main door on the south wall and a west window. */
export function makeTwoRoomPlanWithOpenings(): ProjectDoc {
  const doc = makeTwoRoomPlan();
  return applyGroup(doc, [
    {
      type: 'opening.add',
      payload: {
        id: FIXTURE_IDS.doorMain,
        wallId: FIXTURE_IDS.wallSouth,
        kind: 'door',
        widthMm: DEFAULTS.doorWidthMm,
        heightMm: DEFAULTS.doorHeightMm,
        sillMm: 0,
        offsetMm: 1500,
        swing: 'in-left',
      },
    },
    {
      type: 'opening.add',
      payload: {
        id: FIXTURE_IDS.windowWest,
        wallId: FIXTURE_IDS.wallWest,
        kind: 'window',
        widthMm: DEFAULTS.windowWidthMm,
        heightMm: DEFAULTS.windowHeightMm,
        sillMm: DEFAULTS.sillDefaultMm,
        offsetMm: 2000,
        swing: 'in-left',
      },
    },
  ]).model;
}
