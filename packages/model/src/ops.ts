/**
 * ops.ts — THE OP TAXONOMY (playbook §4). 32 ops, no more, no less.
 *
 * GOLDEN RULE 1: the op is the atom. The UI never mutates state; it dispatches
 * ops. The solver emits ops. The copilot emits ops. If a feature cannot be
 * expressed here, the feature gets redesigned — the taxonomy does not grow
 * casually (it freezes at M1, per the product spec).
 *
 * TWO RULES THAT MAKE `fold` PURE AND `replay` DETERMINISTIC:
 *   1. Ops that CREATE an element carry that element's id in the payload. Ids
 *      are minted by the op *producer* (`newId('wall')`), never inside fold.
 *   2. Ops carry no timestamps, no user names, no random values. `solver.
 *      apply_option` carries its expanded op list so replaying it does not need
 *      the original solver job.
 *
 * OP_CATALOG at the bottom is a MACHINE-READABLE description of every op:
 * §10 requires the copilot system prompt to be generated from it, so it must be
 * data (not doc comments). It is also what `schema/ops.schema.json` mirrors and
 * what the coverage test asserts.
 */

import type { ElementType, Id } from './ids';
import type {
  AnnotationAnchorKind,
  Direction4,
  Direction8,
  FacadeComponentKind,
  JsonObject,
  LevelData,
  OpSource,
  OpeningKind,
  OpeningSwing,
  RailingKind,
  RoomType,
  SizeMm,
  StairKind,
  StairLanding,
  SurfaceGroupRef,
  WallKind,
} from './model';
import {
  ANNOTATION_ANCHOR_KINDS,
  DIRECTIONS_4,
  DIRECTIONS_8,
  OPENING_KINDS,
  OPENING_SWINGS,
  RAILING_KINDS,
  ROOM_TYPES,
  STAIR_KINDS,
  SURFACE_GROUPS,
  WALL_KINDS,
} from './model';
import type { Polygon, Pt } from './geometry';

// ---------------------------------------------------------------------------
// Op envelope
// ---------------------------------------------------------------------------

/**
 * Fields every op may carry.
 *  - `groupId`: undo/redo operates on GROUPS, not single ops. A copilot edit or
 *    `solver.apply_option` is one group.
 *  - `clientOpId`: client-generated idempotency key; the server dedupes on it.
 *  - `source`: provenance. Set by the server; `fold` ignores it.
 */
export interface OpMeta {
  readonly groupId?: string;
  readonly clientOpId?: string;
  readonly source?: OpSource;
}

/** Helper: an op is `{ type, payload }` plus the optional envelope fields. */
export type OpOf<T extends string, P> = OpMeta & {
  readonly type: T;
  readonly payload: P;
};

// ---------------------------------------------------------------------------
// 1–5  plot & brief
// ---------------------------------------------------------------------------

/** 1. `plot.set_boundary` */
export interface PlotSetBoundaryPayload {
  /** CCW ring, integer mm, ≥3 vertices, non-zero area, no self-intersections. */
  readonly polygon: Polygon;
  /** Where the boundary came from: 'manual' | 'dxf' | 'seed'. */
  readonly source?: string;
}

/** 2. `plot.set_north` */
export interface PlotSetNorthPayload {
  /** Integer degrees, 0–359: rotation of true north from +Y, clockwise. */
  readonly deg: number;
}

/** 3. `plot.set_road` */
export interface PlotSetRoadPayload {
  readonly edgeIndex: number;
  /** Road width in mm, or null to remove the road from this edge. */
  readonly widthMm: number | null;
  readonly name?: string | null;
}

/** 4. `plot.set_reg_profile` */
export interface PlotSetRegProfilePayload {
  /** Rule pack id ('blr' | 'ncr' | 'hyd' | …), or null to clear. */
  readonly cityPack: string | null;
  /** Per-project overrides; audited (§13). */
  readonly overrides: JsonObject;
}

/** 5. `brief.update` — RFC 7386 JSON merge patch on `brief.data`. */
export interface BriefUpdatePayload {
  readonly patch: JsonObject;
  /** Optional whole-field updates that live outside `data`. */
  readonly vastuMode?: 'off' | 'advisory' | 'strict';
  readonly completeness?: number;
}

// ---------------------------------------------------------------------------
// 6–8  storeys
// ---------------------------------------------------------------------------

/** 6. `storey.add` */
export interface StoreyAddPayload {
  readonly id: Id<'storey'>;
  /** Insert position; 0 = ground floor. */
  readonly index: number;
  readonly name?: string;
  readonly heightMm: number;
  readonly level?: LevelData;
}

/** 7. `storey.remove` — cascades to that storey's walls/openings/rooms/etc. */
export interface StoreyRemovePayload {
  readonly index: number;
}

/** 8. `storey.set_height` */
export interface StoreySetHeightPayload {
  readonly storeyId: Id<'storey'>;
  readonly heightMm: number;
}

// ---------------------------------------------------------------------------
// 9–13  walls
// ---------------------------------------------------------------------------

/** 9. `wall.add` */
export interface WallAddPayload {
  readonly id: Id<'wall'>;
  readonly storeyId: Id<'storey'>;
  readonly a: Pt;
  readonly b: Pt;
  readonly thicknessMm: number;
  readonly kind: WallKind;
  readonly loadBearing?: boolean;
}

/** 10. `wall.move` — both endpoints; joins re-resolve, rooms re-detect. */
export interface WallMovePayload {
  readonly wallId: Id<'wall'>;
  readonly a: Pt;
  readonly b: Pt;
}

/** 11. `wall.split` — split at `atMm` along the wall from `a`. */
export interface WallSplitPayload {
  readonly wallId: Id<'wall'>;
  readonly atMm: number;
  /** Id for the new second half (the op producer mints it). */
  readonly newWallId: Id<'wall'>;
}

/** 12. `wall.delete` — cascades to openings hosted on the wall. */
export interface WallDeletePayload {
  readonly wallId: Id<'wall'>;
}

/** 13. `wall.set_thickness` */
export interface WallSetThicknessPayload {
  readonly wallId: Id<'wall'>;
  readonly thicknessMm: number;
}

// ---------------------------------------------------------------------------
// 14–18  openings
// ---------------------------------------------------------------------------

/** 14. `opening.add` */
export interface OpeningAddPayload {
  readonly id: Id<'opening'>;
  readonly wallId: Id<'wall'>;
  readonly kind: OpeningKind;
  readonly widthMm: number;
  readonly heightMm: number;
  readonly sillMm: number;
  /** Distance along the wall from `wall.a` to the opening CENTRE. */
  readonly offsetMm: number;
  readonly swing: OpeningSwing;
  readonly tag?: string | null;
}

/** 15. `opening.move` — slide along the wall, or re-host onto another wall. */
export interface OpeningMovePayload {
  readonly openingId: Id<'opening'>;
  readonly offsetMm: number;
  /** Re-host to this wall; omit to keep the current host. */
  readonly wallId?: Id<'wall'>;
}

/** 16. `opening.resize` */
export interface OpeningResizePayload {
  readonly openingId: Id<'opening'>;
  readonly widthMm?: number;
  readonly heightMm?: number;
  readonly sillMm?: number;
}

/** 17. `opening.flip` */
export interface OpeningFlipPayload {
  readonly openingId: Id<'opening'>;
  readonly swing: OpeningSwing;
}

/** 18. `opening.delete` */
export interface OpeningDeletePayload {
  readonly openingId: Id<'opening'>;
}

// ---------------------------------------------------------------------------
// 19–20  rooms (rooms are DERIVED; these ops carry the human intent)
// ---------------------------------------------------------------------------

/** 19. `room.assign` */
export interface RoomAssignPayload {
  readonly roomId: Id<'room'>;
  readonly type: RoomType;
  readonly name?: string;
  readonly tags?: readonly string[];
  /** Lock the room against solver re-solve (§5.7). */
  readonly locked?: boolean;
}

/** 20. `room.set_target` — feeds the solver, not the geometry. */
export interface RoomSetTargetPayload {
  readonly roomId: Id<'room'>;
  readonly targetAreaMm2?: number | null;
  readonly mustFace?: Direction8 | null;
}

// ---------------------------------------------------------------------------
// 21–23  stairs
// ---------------------------------------------------------------------------

/** 21. `stair.add` */
export interface StairAddPayload {
  readonly id: Id<'stair'>;
  readonly storeyId: Id<'storey'>;
  readonly kind: StairKind;
  readonly origin: Pt;
  readonly direction: Direction4;
  readonly riserMm: number;
  readonly treadMm: number;
  readonly widthMm: number;
  readonly risersCount: number;
  readonly landing?: StairLanding | null;
}

/** Editable stair fields (22). */
export interface StairPatch {
  readonly kind?: StairKind;
  readonly origin?: Pt;
  readonly direction?: Direction4;
  readonly riserMm?: number;
  readonly treadMm?: number;
  readonly widthMm?: number;
  readonly risersCount?: number;
  readonly landing?: StairLanding | null;
}

/** 22. `stair.edit` */
export interface StairEditPayload {
  readonly stairId: Id<'stair'>;
  readonly patch: StairPatch;
}

/** 23. `stair.delete` */
export interface StairDeletePayload {
  readonly stairId: Id<'stair'>;
}

// ---------------------------------------------------------------------------
// 24–26  combined action-field ops (one op type, `action` selects the verb)
// ---------------------------------------------------------------------------

export const COLUMN_ACTIONS = ['add', 'move', 'delete'] as const;
export type ColumnAction = (typeof COLUMN_ACTIONS)[number];

/** 24. `column.set` — coordination-only columns. */
export interface ColumnSetPayload {
  readonly action: ColumnAction;
  readonly id: Id<'column'>;
  /** Required for 'add'. */
  readonly storeyId?: Id<'storey'>;
  /** Required for 'add' and 'move'. */
  readonly pt?: Pt;
  /** Optional for 'add' (defaults to 230×230). */
  readonly sizeMm?: SizeMm;
}

export const FURNITURE_ACTIONS = ['place', 'transform', 'delete'] as const;
export type FurnitureAction = (typeof FURNITURE_ACTIONS)[number];

/** 25. `furniture.set` */
export interface FurnitureSetPayload {
  readonly action: FurnitureAction;
  readonly id: Id<'furniture'>;
  /** Required for 'place'. */
  readonly storeyId?: Id<'storey'>;
  /** Required for 'place'. */
  readonly catalogId?: string;
  /** Required for 'place'; optional for 'transform'. */
  readonly pt?: Pt;
  /** Integer degrees CCW. */
  readonly rotationDeg?: number;
}

export const BALCONY_ACTIONS = ['add', 'edit', 'delete'] as const;
export type BalconyAction = (typeof BALCONY_ACTIONS)[number];

/** 26. `balcony.set` */
export interface BalconySetPayload {
  readonly action: BalconyAction;
  readonly id: Id<'balcony'>;
  readonly storeyId?: Id<'storey'>;
  readonly polygon?: Polygon;
  readonly railingKind?: RailingKind;
  readonly railingHeightMm?: number;
  readonly projectionMm?: number;
  readonly slabThicknessMm?: number;
}

// ---------------------------------------------------------------------------
// 27–30  facade, materials, levels
// ---------------------------------------------------------------------------

/** 27. `facade.apply_kit` — REPLACES the whole facade sub-model. */
export interface FacadeApplyKitPayload {
  /** Kit id, or null to clear the facade entirely (this is also the undo form). */
  readonly kitId: string | null;
  readonly seed: number;
  readonly colorwayId?: string | null;
  /**
   * Components the kit generator produced. Carried in the op so that replaying
   * an op log never needs to re-run the generator (determinism, §Golden 4).
   * Empty array = "clear the facade".
   */
  readonly components: readonly FacadeComponentSpec[];
}

/** A facade component as emitted by a kit generator. */
export interface FacadeComponentSpec {
  readonly id: Id<'facadecomp'>;
  readonly kind: FacadeComponentKind;
  readonly storeyId?: Id<'storey'> | null;
  readonly wallId?: Id<'wall'> | null;
  readonly openingId?: Id<'opening'> | null;
  readonly params: JsonObject;
}

/** 28. `facade.edit_component` — RFC 7386 merge patch on the component params. */
export interface FacadeEditComponentPayload {
  readonly componentId: Id<'facadecomp'>;
  readonly patch: JsonObject;
}

/** 29. `material.assign` */
export interface MaterialAssignPayload {
  readonly id: Id<'material'>;
  readonly target: SurfaceGroupRef;
  /** Catalogue material id, or null to clear the assignment. */
  readonly materialId: string | null;
}

/** 30. `levels.set` */
export interface LevelsSetPayload {
  readonly plinthMm?: number;
  readonly sillDefaultMm?: number;
  readonly lintelDefaultMm?: number;
  readonly parapetMm?: number;
  /** Explicit FFL per storey; normally derived from storey heights. */
  readonly fflPerStoreyMm?: readonly number[];
}

// ---------------------------------------------------------------------------
// 31  solver
// ---------------------------------------------------------------------------

/**
 * 31. `solver.apply_option` — expands to an atomic op group.
 *
 * `ops` is the expansion (walls/openings/stairs/room assignments for the chosen
 * option). It is stored in the op so replay never depends on the solver job
 * still existing, and so the diff preview and the applied state cannot diverge.
 */
export interface SolverApplyOptionPayload {
  readonly solverJobId: string;
  readonly optionIndex: number;
  readonly ops: readonly Op[];
  /** Room ids the user locked before re-solving; must survive untouched (§5.7). */
  readonly lockedRoomIds?: readonly Id<'room'>[];
}

// ---------------------------------------------------------------------------
// 32  annotations
// ---------------------------------------------------------------------------

export const ANNOTATION_ACTIONS = ['add', 'edit', 'delete'] as const;
export type AnnotationAction = (typeof ANNOTATION_ACTIONS)[number];

/** 32. `annotation.set` — sheet annotations, anchored to element ids (§7). */
export interface AnnotationSetPayload {
  readonly action: AnnotationAction;
  readonly id: Id<'annotation'>;
  readonly sheetId?: Id<'sheet'>;
  readonly anchorElementId?: string | null;
  readonly anchorKind?: AnnotationAnchorKind;
  readonly payload?: JsonObject;
  readonly orphaned?: boolean;
}

// ---------------------------------------------------------------------------
// The discriminated union
// ---------------------------------------------------------------------------

export type PlotSetBoundaryOp = OpOf<'plot.set_boundary', PlotSetBoundaryPayload>;
export type PlotSetNorthOp = OpOf<'plot.set_north', PlotSetNorthPayload>;
export type PlotSetRoadOp = OpOf<'plot.set_road', PlotSetRoadPayload>;
export type PlotSetRegProfileOp = OpOf<'plot.set_reg_profile', PlotSetRegProfilePayload>;
export type BriefUpdateOp = OpOf<'brief.update', BriefUpdatePayload>;
export type StoreyAddOp = OpOf<'storey.add', StoreyAddPayload>;
export type StoreyRemoveOp = OpOf<'storey.remove', StoreyRemovePayload>;
export type StoreySetHeightOp = OpOf<'storey.set_height', StoreySetHeightPayload>;
export type WallAddOp = OpOf<'wall.add', WallAddPayload>;
export type WallMoveOp = OpOf<'wall.move', WallMovePayload>;
export type WallSplitOp = OpOf<'wall.split', WallSplitPayload>;
export type WallDeleteOp = OpOf<'wall.delete', WallDeletePayload>;
export type WallSetThicknessOp = OpOf<'wall.set_thickness', WallSetThicknessPayload>;
export type OpeningAddOp = OpOf<'opening.add', OpeningAddPayload>;
export type OpeningMoveOp = OpOf<'opening.move', OpeningMovePayload>;
export type OpeningResizeOp = OpOf<'opening.resize', OpeningResizePayload>;
export type OpeningFlipOp = OpOf<'opening.flip', OpeningFlipPayload>;
export type OpeningDeleteOp = OpOf<'opening.delete', OpeningDeletePayload>;
export type RoomAssignOp = OpOf<'room.assign', RoomAssignPayload>;
export type RoomSetTargetOp = OpOf<'room.set_target', RoomSetTargetPayload>;
export type StairAddOp = OpOf<'stair.add', StairAddPayload>;
export type StairEditOp = OpOf<'stair.edit', StairEditPayload>;
export type StairDeleteOp = OpOf<'stair.delete', StairDeletePayload>;
export type ColumnSetOp = OpOf<'column.set', ColumnSetPayload>;
export type FurnitureSetOp = OpOf<'furniture.set', FurnitureSetPayload>;
export type BalconySetOp = OpOf<'balcony.set', BalconySetPayload>;
export type FacadeApplyKitOp = OpOf<'facade.apply_kit', FacadeApplyKitPayload>;
export type FacadeEditComponentOp = OpOf<'facade.edit_component', FacadeEditComponentPayload>;
export type MaterialAssignOp = OpOf<'material.assign', MaterialAssignPayload>;
export type LevelsSetOp = OpOf<'levels.set', LevelsSetPayload>;
export type SolverApplyOptionOp = OpOf<'solver.apply_option', SolverApplyOptionPayload>;
export type AnnotationSetOp = OpOf<'annotation.set', AnnotationSetPayload>;

/** Every mutation in the product. 32 members, matching playbook §4 1:1. */
export type Op =
  | PlotSetBoundaryOp
  | PlotSetNorthOp
  | PlotSetRoadOp
  | PlotSetRegProfileOp
  | BriefUpdateOp
  | StoreyAddOp
  | StoreyRemoveOp
  | StoreySetHeightOp
  | WallAddOp
  | WallMoveOp
  | WallSplitOp
  | WallDeleteOp
  | WallSetThicknessOp
  | OpeningAddOp
  | OpeningMoveOp
  | OpeningResizeOp
  | OpeningFlipOp
  | OpeningDeleteOp
  | RoomAssignOp
  | RoomSetTargetOp
  | StairAddOp
  | StairEditOp
  | StairDeleteOp
  | ColumnSetOp
  | FurnitureSetOp
  | BalconySetOp
  | FacadeApplyKitOp
  | FacadeEditComponentOp
  | MaterialAssignOp
  | LevelsSetOp
  | SolverApplyOptionOp
  | AnnotationSetOp;

export type OpType = Op['type'];

/** Narrow an Op by its type tag: `Extract<Op, { type: 'wall.add' }>`. */
export type OpByType<T extends OpType> = Extract<Op, { type: T }>;

/** A batch applied atomically under one `groupId`. */
export interface OpGroup {
  readonly groupId: string;
  readonly ops: readonly Op[];
}

// ---------------------------------------------------------------------------
// OP_CATALOG — machine-readable op description (§10 generates the prompt here)
// ---------------------------------------------------------------------------

export type OpCategory =
  | 'plot'
  | 'brief'
  | 'storey'
  | 'wall'
  | 'opening'
  | 'room'
  | 'stair'
  | 'column'
  | 'furniture'
  | 'balcony'
  | 'facade'
  | 'material'
  | 'levels'
  | 'solver'
  | 'annotation';

export type OpFieldType =
  | 'int-mm'
  | 'int-mm2'
  | 'int-deg'
  | 'int'
  | 'string'
  | 'bool'
  | 'enum'
  | 'pt'
  | 'polygon'
  | 'id'
  | 'json'
  | 'ops'
  | 'level-data'
  | 'landing'
  | 'size-mm'
  | 'int-mm-array'
  | 'string-array'
  | 'id-array'
  | 'surface-group-ref'
  | 'facade-components';

export interface OpFieldSpec {
  readonly name: string;
  readonly type: OpFieldType;
  readonly required: boolean;
  /** Physical unit, so a prompt/validator never has to guess. */
  readonly units: 'mm' | 'mm2' | 'deg' | 'count' | 'index' | null;
  /** For `type: 'id'` — which id namespace the value must belong to. */
  readonly idType?: ElementType;
  /** For `type: 'enum'` — the exact allowed values. */
  readonly enumValues?: readonly string[];
  /** May the field be explicitly null? */
  readonly nullable?: boolean;
  readonly description: string;
}

export interface OpSpec {
  /** Row number in playbook §4 (1–32). */
  readonly number: number;
  readonly type: OpType;
  readonly category: OpCategory;
  /** Imperative title: "Add wall". */
  readonly title: string;
  /** One-line human summary — goes verbatim into the copilot system prompt. */
  readonly summary: string;
  readonly payload: readonly OpFieldSpec[];
  /** For combined ops (24/25/26/32): the values `payload.action` accepts. */
  readonly actions: readonly string[] | null;
  readonly creates: readonly ElementType[];
  readonly destroys: readonly ElementType[];
  /** May the copilot emit this op? (§10: solver expansion and plot edits may not.) */
  readonly copilot: boolean;
  /** Must always be applied inside a group (atomic). */
  readonly atomic: boolean;
  /** A valid, hand-checked example — the schema tests fold every one of these. */
  readonly example: Op;
}

function exampleId<T extends ElementType>(type: T, tag: string): Id<T> {
  const clean = tag.toUpperCase().replace(/[^0-9ABCDEFGHJKMNPQRSTVWXYZ]/g, '');
  const body = `01J${'0'.repeat(26)}`.slice(0, 26 - clean.length) + clean;
  return `${type}_${body}`;
}

/** Stable example ids so fixtures and docs read the same every time. */
export const EXAMPLE_IDS = {
  storey0: exampleId('storey', 'GF'),
  storey1: exampleId('storey', 'FF'),
  wall1: exampleId('wall', 'W1'),
  wall2: exampleId('wall', 'W2'),
  opening1: exampleId('opening', 'D1'),
  room1: exampleId('room', 'R1'),
  stair1: exampleId('stair', 'S1'),
  column1: exampleId('column', 'C1'),
  furniture1: exampleId('furniture', 'F1'),
  balcony1: exampleId('balcony', 'B1'),
  facadeComp1: exampleId('facadecomp', 'FC1'),
  material1: exampleId('material', 'M1'),
  annotation1: exampleId('annotation', 'A1'),
  sheet1: exampleId('sheet', 'SH1'),
} as const;

const F = {
  intMm: (name: string, description: string, required = true): OpFieldSpec => ({
    name,
    type: 'int-mm',
    required,
    units: 'mm',
    description,
  }),
  int: (
    name: string,
    description: string,
    units: OpFieldSpec['units'] = 'count',
    required = true,
  ): OpFieldSpec => ({ name, type: 'int', required, units, description }),
  id: (name: string, idType: ElementType, description: string, required = true): OpFieldSpec => ({
    name,
    type: 'id',
    required,
    units: null,
    idType,
    description,
  }),
  pt: (name: string, description: string, required = true): OpFieldSpec => ({
    name,
    type: 'pt',
    required,
    units: 'mm',
    description,
  }),
  polygon: (name: string, description: string, required = true): OpFieldSpec => ({
    name,
    type: 'polygon',
    required,
    units: 'mm',
    description,
  }),
  enum: (
    name: string,
    enumValues: readonly string[],
    description: string,
    required = true,
    nullable = false,
  ): OpFieldSpec => ({
    name,
    type: 'enum',
    required,
    units: null,
    enumValues,
    nullable,
    description,
  }),
  string: (name: string, description: string, required = true, nullable = false): OpFieldSpec => ({
    name,
    type: 'string',
    required,
    units: null,
    nullable,
    description,
  }),
  bool: (name: string, description: string, required = false): OpFieldSpec => ({
    name,
    type: 'bool',
    required,
    units: null,
    description,
  }),
  json: (name: string, description: string, required = true): OpFieldSpec => ({
    name,
    type: 'json',
    required,
    units: null,
    description,
  }),
};

/**
 * THE SINGLE SOURCE OF TRUTH FOR OP COVERAGE.
 *
 * Consumers:
 *  - `apps/api` copilot: system prompt generated by `renderOpCatalogForPrompt()`
 *  - `schema/ops.schema.json`: hand-kept in lockstep, asserted by a test
 *  - `ops.test.ts`: asserts every `OpType` appears exactly once and every
 *    `example` folds cleanly onto a demo document
 */
export const OP_CATALOG: readonly OpSpec[] = [
  {
    number: 1,
    type: 'plot.set_boundary',
    category: 'plot',
    title: 'Set plot boundary',
    summary: 'Replace the plot boundary polygon (CCW, integer mm, closed, area > 0).',
    payload: [
      F.polygon(
        'polygon',
        'Plot boundary ring, origin at the plot SW corner. An EMPTY array clears the boundary (the undo form of this op); anything else must be a closed ring with area > 0.',
      ),
      F.string('source', "How it was captured: 'manual' | 'dxf' | 'seed'.", false),
    ],
    actions: null,
    creates: [],
    destroys: [],
    copilot: false,
    atomic: false,
    example: {
      type: 'plot.set_boundary',
      payload: {
        polygon: [
          { x: 0, y: 0 },
          { x: 9144, y: 0 },
          { x: 9144, y: 12192 },
          { x: 0, y: 12192 },
        ],
        source: 'manual',
      },
    },
  },
  {
    number: 2,
    type: 'plot.set_north',
    category: 'plot',
    title: 'Set north',
    summary: 'Rotate true north. Integer degrees 0–359, clockwise from +Y.',
    payload: [
      {
        name: 'deg',
        type: 'int-deg',
        required: true,
        units: 'deg',
        description: 'Rotation of true north from +Y, clockwise, 0–359.',
      },
    ],
    actions: null,
    creates: [],
    destroys: [],
    copilot: true,
    atomic: false,
    example: { type: 'plot.set_north', payload: { deg: 0 } },
  },
  {
    number: 3,
    type: 'plot.set_road',
    category: 'plot',
    title: 'Set road on plot edge',
    summary: 'Attach or remove the abutting road width on one plot edge (drives setback tables).',
    payload: [
      F.int('edgeIndex', 'Boundary edge index (boundary[i] -> boundary[i+1]).', 'index'),
      { ...F.intMm('widthMm', 'Road width, or null for no road.'), nullable: true },
      F.string('name', 'Road name for the site plan.', false, true),
    ],
    actions: null,
    creates: [],
    destroys: [],
    copilot: true,
    atomic: false,
    example: { type: 'plot.set_road', payload: { edgeIndex: 0, widthMm: 9000, name: '9m Road' } },
  },
  {
    number: 4,
    type: 'plot.set_reg_profile',
    category: 'plot',
    title: 'Set regulatory profile',
    summary: 'Choose the city rule pack and per-project overrides. Overrides are audited.',
    payload: [
      F.string('cityPack', "Rule pack id: 'blr' | 'ncr' | 'hyd', or null.", true, true),
      F.json('overrides', 'Per-project overrides of pack values.'),
    ],
    actions: null,
    creates: [],
    destroys: [],
    copilot: false,
    atomic: false,
    example: { type: 'plot.set_reg_profile', payload: { cityPack: 'blr', overrides: {} } },
  },
  {
    number: 5,
    type: 'brief.update',
    category: 'brief',
    title: 'Update brief',
    summary: 'Apply an RFC 7386 JSON merge patch to the brief data (null deletes a key).',
    payload: [
      F.json('patch', 'RFC 7386 merge patch applied to brief.data.'),
      F.enum('vastuMode', ['off', 'advisory', 'strict'], 'Vastu mode.', false),
      F.int('completeness', 'Brief completeness meter, 0–100.', 'count', false),
    ],
    actions: null,
    creates: [],
    destroys: [],
    copilot: true,
    atomic: false,
    example: { type: 'brief.update', payload: { patch: { bedrooms: 3 } } },
  },
  {
    number: 6,
    type: 'storey.add',
    category: 'storey',
    title: 'Add storey',
    summary: 'Insert a storey at `index` (0 = ground). FFLs re-derive from storey heights.',
    payload: [
      F.id('id', 'storey', 'Id for the new storey (minted by the caller).'),
      F.int('index', 'Insert position; 0 = ground floor.', 'index'),
      F.string('name', 'Display name, e.g. "First Floor".', false),
      F.intMm('heightMm', 'Floor-to-floor height.'),
      {
        name: 'level',
        type: 'level-data',
        required: false,
        units: 'mm',
        description:
          'Level data override { fflMm, slabThicknessMm, sillDefaultMm, lintelDefaultMm }.',
      },
    ],
    actions: null,
    creates: ['storey'],
    destroys: [],
    copilot: true,
    atomic: false,
    example: {
      type: 'storey.add',
      payload: { id: EXAMPLE_IDS.storey1, index: 1, name: 'First Floor', heightMm: 3000 },
    },
  },
  {
    number: 7,
    type: 'storey.remove',
    category: 'storey',
    title: 'Remove storey',
    summary: 'Remove the storey at `index` and everything on it (walls, openings, rooms, stairs).',
    payload: [F.int('index', 'Storey index to remove.', 'index')],
    actions: null,
    creates: [],
    destroys: [
      'storey',
      'wall',
      'opening',
      'room',
      'stair',
      'slab',
      'column',
      'furniture',
      'balcony',
    ],
    copilot: true,
    atomic: false,
    example: { type: 'storey.remove', payload: { index: 1 } },
  },
  {
    number: 8,
    type: 'storey.set_height',
    category: 'storey',
    title: 'Set storey height',
    summary: 'Change one storey floor-to-floor height; FFLs above it shift, stairs re-check.',
    payload: [
      F.id('storeyId', 'storey', 'Storey to change.'),
      F.intMm('heightMm', 'New floor-to-floor height.'),
    ],
    actions: null,
    creates: [],
    destroys: [],
    copilot: true,
    atomic: false,
    example: {
      type: 'storey.set_height',
      payload: { storeyId: EXAMPLE_IDS.storey0, heightMm: 3200 },
    },
  },
  {
    number: 9,
    type: 'wall.add',
    category: 'wall',
    title: 'Add wall',
    summary: 'Add a wall centreline on a storey. Rooms re-detect afterwards.',
    payload: [
      F.id('id', 'wall', 'Id for the new wall.'),
      F.id('storeyId', 'storey', 'Host storey.'),
      F.pt('a', 'Centreline start.'),
      F.pt('b', 'Centreline end.'),
      F.intMm('thicknessMm', 'Wall thickness: 115 / 150 / 200 / 230 typical.'),
      F.enum('kind', WALL_KINDS, 'Wall kind.'),
      F.bool('loadBearing', 'Structural coordination hint.'),
    ],
    actions: null,
    creates: ['wall'],
    destroys: [],
    copilot: true,
    atomic: false,
    example: {
      type: 'wall.add',
      payload: {
        id: EXAMPLE_IDS.wall1,
        storeyId: EXAMPLE_IDS.storey0,
        a: { x: 0, y: 0 },
        b: { x: 4000, y: 0 },
        thicknessMm: 230,
        kind: 'external',
      },
    },
  },
  {
    number: 10,
    type: 'wall.move',
    category: 'wall',
    title: 'Move wall',
    summary: 'Set both wall endpoints. Joins re-resolve and rooms re-detect (ids preserved).',
    payload: [
      F.id('wallId', 'wall', 'Wall to move.'),
      F.pt('a', 'New centreline start.'),
      F.pt('b', 'New centreline end.'),
    ],
    actions: null,
    creates: [],
    destroys: [],
    copilot: true,
    atomic: false,
    example: {
      type: 'wall.move',
      payload: { wallId: EXAMPLE_IDS.wall1, a: { x: 0, y: 115 }, b: { x: 4000, y: 115 } },
    },
  },
  {
    number: 11,
    type: 'wall.split',
    category: 'wall',
    title: 'Split wall',
    summary:
      'Split a wall at `atMm` from its `a` end into two walls; openings re-host by position.',
    payload: [
      F.id('wallId', 'wall', 'Wall to split.'),
      F.intMm('atMm', 'Distance from `a` at which to split (0 < atMm < length).'),
      F.id('newWallId', 'wall', 'Id for the second half.'),
    ],
    actions: null,
    creates: ['wall'],
    destroys: [],
    copilot: true,
    atomic: false,
    example: {
      type: 'wall.split',
      payload: { wallId: EXAMPLE_IDS.wall1, atMm: 2000, newWallId: EXAMPLE_IDS.wall2 },
    },
  },
  {
    number: 12,
    type: 'wall.delete',
    category: 'wall',
    title: 'Delete wall',
    summary:
      'Delete a wall and every opening hosted on it. Rooms re-detect (merged rooms lose one id).',
    payload: [F.id('wallId', 'wall', 'Wall to delete.')],
    actions: null,
    creates: [],
    destroys: ['wall', 'opening'],
    copilot: true,
    atomic: false,
    example: { type: 'wall.delete', payload: { wallId: EXAMPLE_IDS.wall2 } },
  },
  {
    number: 13,
    type: 'wall.set_thickness',
    category: 'wall',
    title: 'Set wall thickness',
    summary: 'Change wall thickness. Room clear areas shrink/grow accordingly.',
    payload: [F.id('wallId', 'wall', 'Wall to change.'), F.intMm('thicknessMm', 'New thickness.')],
    actions: null,
    creates: [],
    destroys: [],
    copilot: true,
    atomic: false,
    example: {
      type: 'wall.set_thickness',
      payload: { wallId: EXAMPLE_IDS.wall1, thicknessMm: 115 },
    },
  },
  {
    number: 14,
    type: 'opening.add',
    category: 'opening',
    title: 'Add opening',
    summary:
      'Host a door/window/ventilator on a wall. `offsetMm` is to the opening CENTRE from wall.a; must keep 115mm end margins.',
    payload: [
      F.id('id', 'opening', 'Id for the new opening.'),
      F.id('wallId', 'wall', 'Host wall.'),
      F.enum('kind', OPENING_KINDS, 'Opening kind.'),
      F.intMm('widthMm', 'Clear width.'),
      F.intMm('heightMm', 'Clear height.'),
      F.intMm('sillMm', 'Sill height above FFL (0 for doors).'),
      F.intMm('offsetMm', 'Distance along the wall from `a` to the opening centre.'),
      F.enum('swing', OPENING_SWINGS, 'Leaf swing.'),
      F.string(
        'tag',
        'Schedule tag (D1/W2/V1); usually assigned by the schedule generator.',
        false,
        true,
      ),
    ],
    actions: null,
    creates: ['opening'],
    destroys: [],
    copilot: true,
    atomic: false,
    example: {
      type: 'opening.add',
      payload: {
        id: EXAMPLE_IDS.opening1,
        wallId: EXAMPLE_IDS.wall1,
        kind: 'door',
        widthMm: 900,
        heightMm: 2100,
        sillMm: 0,
        offsetMm: 1200,
        swing: 'in-left',
      },
    },
  },
  {
    number: 15,
    type: 'opening.move',
    category: 'opening',
    title: 'Move opening',
    summary:
      'Slide an opening along its wall, or re-host it onto another wall by passing `wallId`.',
    payload: [
      F.id('openingId', 'opening', 'Opening to move.'),
      F.intMm('offsetMm', 'New centre offset from the host wall `a`.'),
      F.id('wallId', 'wall', 'New host wall (omit to keep the current one).', false),
    ],
    actions: null,
    creates: [],
    destroys: [],
    copilot: true,
    atomic: false,
    example: { type: 'opening.move', payload: { openingId: EXAMPLE_IDS.opening1, offsetMm: 1500 } },
  },
  {
    number: 16,
    type: 'opening.resize',
    category: 'opening',
    title: 'Resize opening',
    summary: 'Change width / height / sill of an opening. Omitted fields stay as they are.',
    payload: [
      F.id('openingId', 'opening', 'Opening to resize.'),
      F.intMm('widthMm', 'New clear width.', false),
      F.intMm('heightMm', 'New clear height.', false),
      F.intMm('sillMm', 'New sill height above FFL.', false),
    ],
    actions: null,
    creates: [],
    destroys: [],
    copilot: true,
    atomic: false,
    example: {
      type: 'opening.resize',
      payload: { openingId: EXAMPLE_IDS.opening1, widthMm: 1200 },
    },
  },
  {
    number: 17,
    type: 'opening.flip',
    category: 'opening',
    title: 'Flip opening swing',
    summary: 'Change the door swing / hand.',
    payload: [
      F.id('openingId', 'opening', 'Opening to flip.'),
      F.enum('swing', OPENING_SWINGS, 'New swing.'),
    ],
    actions: null,
    creates: [],
    destroys: [],
    copilot: true,
    atomic: false,
    example: {
      type: 'opening.flip',
      payload: { openingId: EXAMPLE_IDS.opening1, swing: 'in-right' },
    },
  },
  {
    number: 18,
    type: 'opening.delete',
    category: 'opening',
    title: 'Delete opening',
    summary: 'Remove an opening from its wall.',
    payload: [F.id('openingId', 'opening', 'Opening to delete.')],
    actions: null,
    creates: [],
    destroys: ['opening'],
    copilot: true,
    atomic: false,
    example: { type: 'opening.delete', payload: { openingId: EXAMPLE_IDS.opening1 } },
  },
  {
    number: 19,
    type: 'room.assign',
    category: 'room',
    title: 'Assign room type',
    summary:
      'Set a detected room’s programme type, name, tags and lock flag. Never changes geometry.',
    payload: [
      F.id('roomId', 'room', 'Room to assign.'),
      F.enum('type', ROOM_TYPES, 'Programme type.'),
      F.string('name', 'Display name; empty string falls back to the type label.', false),
      {
        name: 'tags',
        type: 'string-array',
        required: false,
        units: null,
        description: 'Free-form tags (e.g. "attached", "guest").',
      },
      F.bool('locked', 'Lock against solver re-solve (§5.7).'),
    ],
    actions: null,
    creates: [],
    destroys: [],
    copilot: true,
    atomic: false,
    example: {
      type: 'room.assign',
      payload: { roomId: EXAMPLE_IDS.room1, type: 'bedroom_master', name: 'Master Bedroom' },
    },
  },
  {
    number: 20,
    type: 'room.set_target',
    category: 'room',
    title: 'Set room target',
    summary:
      'Set a target area and/or required facing for a room. Feeds the solver, not the geometry.',
    payload: [
      F.id('roomId', 'room', 'Room to constrain.'),
      {
        ...F.intMm('targetAreaMm2', 'Target clear area in mm² (null clears it).', false),
        type: 'int-mm2',
        units: 'mm2',
        nullable: true,
      },
      F.enum('mustFace', DIRECTIONS_8, 'Required facing (null clears it).', false, true),
    ],
    actions: null,
    creates: [],
    destroys: [],
    copilot: true,
    atomic: false,
    example: {
      type: 'room.set_target',
      payload: { roomId: EXAMPLE_IDS.room1, targetAreaMm2: 12_000_000, mustFace: 'NE' },
    },
  },
  {
    number: 21,
    type: 'stair.add',
    category: 'stair',
    title: 'Add stair',
    summary:
      'Add a stair. risersCount × riserMm must equal the storey height within ±10mm, or the op is rejected.',
    payload: [
      F.id('id', 'stair', 'Id for the new stair.'),
      F.id('storeyId', 'storey', 'Storey the flight starts on.'),
      F.enum('kind', STAIR_KINDS, 'Stair configuration.'),
      F.pt('origin', 'Footprint origin (first riser).'),
      F.enum('direction', DIRECTIONS_4, 'Direction of travel going up.'),
      F.intMm('riserMm', 'Riser height (NBC ≤ 190).'),
      F.intMm('treadMm', 'Tread depth (NBC ≥ 250).'),
      F.intMm('widthMm', 'Clear flight width (NBC ≥ 900).'),
      F.int('risersCount', 'Number of risers.'),
      {
        name: 'landing',
        type: 'landing',
        required: false,
        units: 'mm',
        nullable: true,
        description: 'Landing block { widthMm, depthMm }, or null for a single straight flight.',
      },
    ],
    actions: null,
    creates: ['stair'],
    destroys: [],
    copilot: true,
    atomic: false,
    example: {
      type: 'stair.add',
      payload: {
        id: EXAMPLE_IDS.stair1,
        storeyId: EXAMPLE_IDS.storey0,
        kind: 'dogleg',
        origin: { x: 1000, y: 1000 },
        direction: 'N',
        riserMm: 167,
        treadMm: 275,
        widthMm: 1000,
        risersCount: 18,
        landing: { widthMm: 2115, depthMm: 1000 },
      },
    },
  },
  {
    number: 22,
    type: 'stair.edit',
    category: 'stair',
    title: 'Edit stair',
    summary:
      'Patch stair fields (kind, origin, direction, riser, tread, width, risersCount, landing).',
    payload: [
      F.id('stairId', 'stair', 'Stair to edit.'),
      F.json('patch', 'Partial stair fields; omitted fields are unchanged.'),
    ],
    actions: null,
    creates: [],
    destroys: [],
    copilot: true,
    atomic: false,
    example: {
      type: 'stair.edit',
      payload: { stairId: EXAMPLE_IDS.stair1, patch: { widthMm: 1050 } },
    },
  },
  {
    number: 23,
    type: 'stair.delete',
    category: 'stair',
    title: 'Delete stair',
    summary: 'Remove a stair.',
    payload: [F.id('stairId', 'stair', 'Stair to delete.')],
    actions: null,
    creates: [],
    destroys: ['stair'],
    copilot: true,
    atomic: false,
    example: { type: 'stair.delete', payload: { stairId: EXAMPLE_IDS.stair1 } },
  },
  {
    number: 24,
    type: 'column.set',
    category: 'column',
    title: 'Add / move / delete column',
    summary:
      'One op for all column edits; `action` selects add | move | delete. Columns are coordination-only (never affect rooms).',
    payload: [
      F.enum('action', COLUMN_ACTIONS, 'add | move | delete.'),
      F.id('id', 'column', 'Column id (new id for add, existing for move/delete).'),
      F.id('storeyId', 'storey', 'Host storey (required for add).', false),
      F.pt('pt', 'Column centre (required for add and move).', false),
      {
        name: 'sizeMm',
        type: 'size-mm',
        required: false,
        units: 'mm',
        description: 'Column size { xMm, yMm }; defaults to 230×230 on add.',
      },
    ],
    actions: [...COLUMN_ACTIONS],
    creates: ['column'],
    destroys: ['column'],
    copilot: true,
    atomic: false,
    example: {
      type: 'column.set',
      payload: {
        action: 'add',
        id: EXAMPLE_IDS.column1,
        storeyId: EXAMPLE_IDS.storey0,
        pt: { x: 3000, y: 3000 },
        sizeMm: { xMm: 230, yMm: 230 },
      },
    },
  },
  {
    number: 25,
    type: 'furniture.set',
    category: 'furniture',
    title: 'Place / transform / delete furniture',
    summary: 'One op for all furniture edits; `action` selects place | transform | delete.',
    payload: [
      F.enum('action', FURNITURE_ACTIONS, 'place | transform | delete.'),
      F.id('id', 'furniture', 'Furniture instance id.'),
      F.id('storeyId', 'storey', 'Host storey (required for place).', false),
      F.string('catalogId', 'Furniture catalogue id (required for place).', false),
      F.pt('pt', 'Footprint centre.', false),
      {
        name: 'rotationDeg',
        type: 'int-deg',
        required: false,
        units: 'deg',
        description: 'Integer degrees CCW from the catalogue default orientation.',
      },
    ],
    actions: [...FURNITURE_ACTIONS],
    creates: ['furniture'],
    destroys: ['furniture'],
    copilot: true,
    atomic: false,
    example: {
      type: 'furniture.set',
      payload: {
        action: 'place',
        id: EXAMPLE_IDS.furniture1,
        storeyId: EXAMPLE_IDS.storey0,
        catalogId: 'bed-queen-1900x1525',
        pt: { x: 2000, y: 2000 },
        rotationDeg: 90,
      },
    },
  },
  {
    number: 26,
    type: 'balcony.set',
    category: 'balcony',
    title: 'Add / edit / delete balcony',
    summary:
      'One op for all balcony edits; `action` selects add | edit | delete. Projection is checked against the projection rules.',
    payload: [
      F.enum('action', BALCONY_ACTIONS, 'add | edit | delete.'),
      F.id('id', 'balcony', 'Balcony id.'),
      F.id('storeyId', 'storey', 'Host storey (required for add).', false),
      F.polygon('polygon', 'Balcony slab outline (required for add).', false),
      F.enum('railingKind', RAILING_KINDS, 'Railing type.', false),
      F.intMm('railingHeightMm', 'Railing height (1000 default).', false),
      F.intMm('projectionMm', 'Projection beyond the building line.', false),
      F.intMm('slabThicknessMm', 'Balcony slab thickness.', false),
    ],
    actions: [...BALCONY_ACTIONS],
    creates: ['balcony'],
    destroys: ['balcony'],
    copilot: true,
    atomic: false,
    example: {
      type: 'balcony.set',
      payload: {
        action: 'add',
        id: EXAMPLE_IDS.balcony1,
        storeyId: EXAMPLE_IDS.storey0,
        polygon: [
          { x: 0, y: 0 },
          { x: 2400, y: 0 },
          { x: 2400, y: 900 },
          { x: 0, y: 900 },
        ],
        railingKind: 'ms',
        railingHeightMm: 1000,
        projectionMm: 900,
      },
    },
  },
  {
    number: 27,
    type: 'facade.apply_kit',
    category: 'facade',
    title: 'Apply facade kit',
    summary:
      'Replace the whole facade sub-model with a kit instantiation. Cannot touch walls, rooms or areas.',
    payload: [
      F.string(
        'kitId',
        "Facade kit id: 'contemporary' | 'modern-minimal', or null to clear.",
        true,
        true,
      ),
      F.int('seed', 'Variation seed.', 'count'),
      F.string('colorwayId', 'Colorway id, or null.', false, true),
      {
        name: 'components',
        type: 'facade-components',
        required: true,
        units: null,
        description: 'Components the kit generator produced (carried so replay is deterministic).',
      },
    ],
    actions: null,
    creates: ['facadecomp'],
    destroys: ['facadecomp'],
    copilot: true,
    atomic: true,
    example: {
      type: 'facade.apply_kit',
      payload: { kitId: 'contemporary', seed: 7, colorwayId: 'mono-wood', components: [] },
    },
  },
  {
    number: 28,
    type: 'facade.edit_component',
    category: 'facade',
    title: 'Edit facade component',
    summary: 'RFC 7386 merge patch on one facade component’s params (e.g. chajja projection).',
    payload: [
      F.id('componentId', 'facadecomp', 'Component to edit.'),
      F.json('patch', 'Merge patch on the component params.'),
    ],
    actions: null,
    creates: [],
    destroys: [],
    copilot: true,
    atomic: false,
    example: {
      type: 'facade.edit_component',
      payload: { componentId: EXAMPLE_IDS.facadeComp1, patch: { projectionMm: 750 } },
    },
  },
  {
    number: 29,
    type: 'material.assign',
    category: 'material',
    title: 'Assign material',
    summary:
      'Assign a catalogue material to a surface group (optionally scoped to a storey or element).',
    payload: [
      F.id('id', 'material', 'Assignment id.'),
      {
        name: 'target',
        type: 'surface-group-ref',
        required: true,
        units: null,
        enumValues: SURFACE_GROUPS,
        description: 'Target { group, storeyId|null, elementId|null }.',
      },
      F.string('materialId', 'Catalogue material id, or null to clear.', true, true),
    ],
    actions: null,
    creates: ['material'],
    destroys: ['material'],
    copilot: true,
    atomic: false,
    example: {
      type: 'material.assign',
      payload: {
        id: EXAMPLE_IDS.material1,
        target: { group: 'external_wall', storeyId: null, elementId: null },
        materialId: 'texture-paint-grey',
      },
    },
  },
  {
    number: 30,
    type: 'levels.set',
    category: 'levels',
    title: 'Set levels',
    summary: 'Set plinth / default sill / default lintel / parapet heights (sections read these).',
    payload: [
      F.intMm('plinthMm', 'Plinth height above ground.', false),
      F.intMm('sillDefaultMm', 'Default window sill above FFL.', false),
      F.intMm('lintelDefaultMm', 'Default lintel height above FFL.', false),
      F.intMm('parapetMm', 'Terrace parapet height.', false),
      {
        name: 'fflPerStoreyMm',
        type: 'int-mm-array',
        required: false,
        units: 'mm',
        description: 'Explicit FFL per storey; normally derived from storey heights.',
      },
    ],
    actions: null,
    creates: [],
    destroys: [],
    copilot: true,
    atomic: false,
    example: { type: 'levels.set', payload: { plinthMm: 600, parapetMm: 1050 } },
  },
  {
    number: 31,
    type: 'solver.apply_option',
    category: 'solver',
    title: 'Apply solver option',
    summary:
      'Apply a generated plan option as ONE atomic group. Carries its own expansion so replay never re-runs the solver.',
    payload: [
      F.string('solverJobId', 'Solver job the option came from.'),
      F.int('optionIndex', 'Index of the chosen option.', 'index'),
      {
        name: 'ops',
        type: 'ops',
        required: true,
        units: null,
        description: 'The expansion: the ops that build the option (applied atomically).',
      },
      {
        name: 'lockedRoomIds',
        type: 'id-array',
        required: false,
        units: null,
        idType: 'room',
        description: 'Room ids the user locked; the solver must return them untouched.',
      },
    ],
    actions: null,
    creates: [],
    destroys: [],
    copilot: false,
    atomic: true,
    example: {
      type: 'solver.apply_option',
      payload: { solverJobId: 'job_demo', optionIndex: 0, ops: [] },
    },
  },
  {
    number: 32,
    type: 'annotation.set',
    category: 'annotation',
    title: 'Add / edit / delete sheet annotation',
    summary:
      'One op for all sheet annotations; `action` selects add | edit | delete. Annotations anchor to element ids.',
    payload: [
      F.enum('action', ANNOTATION_ACTIONS, 'add | edit | delete.'),
      F.id('id', 'annotation', 'Annotation id.'),
      F.id('sheetId', 'sheet', 'Sheet the annotation lives on (required for add).', false),
      F.string(
        'anchorElementId',
        'Model element the annotation is anchored to, or null.',
        false,
        true,
      ),
      F.enum('anchorKind', ANNOTATION_ANCHOR_KINDS, 'What kind of thing the anchor is.', false),
      F.json('payload', 'Annotation content (text, leader, style).', false),
      F.bool('orphaned', 'Set true when a re-solve destroyed the anchor (Review Tray).'),
    ],
    actions: [...ANNOTATION_ACTIONS],
    creates: ['annotation'],
    destroys: ['annotation'],
    copilot: false,
    atomic: false,
    example: {
      type: 'annotation.set',
      payload: {
        action: 'add',
        id: EXAMPLE_IDS.annotation1,
        sheetId: EXAMPLE_IDS.sheet1,
        anchorElementId: EXAMPLE_IDS.wall1,
        anchorKind: 'wall',
        payload: { text: 'RCC beam over — refer structural' },
      },
    },
  },
];

/** All 32 op type strings, in playbook order. */
export const OP_TYPES: readonly OpType[] = OP_CATALOG.map((s) => s.type);

const OP_SPEC_BY_TYPE: ReadonlyMap<string, OpSpec> = new Map(OP_CATALOG.map((s) => [s.type, s]));

/** Catalogue entry for an op type, or undefined for an unknown type. */
export function getOpSpec(type: string): OpSpec | undefined {
  return OP_SPEC_BY_TYPE.get(type);
}

/** Type guard for the op-type tag. */
export function isOpType(value: unknown): value is OpType {
  return typeof value === 'string' && OP_SPEC_BY_TYPE.has(value);
}

/**
 * Shallow runtime guard: right envelope, known type, payload is an object.
 * DEEP validation (units, ids, geometry, invariants) lives in validate.ts —
 * this only decides "is this thing shaped like an op at all".
 */
export function isOp(value: unknown): value is Op {
  if (typeof value !== 'object' || value === null) return false;
  const v = value as { type?: unknown; payload?: unknown };
  if (!isOpType(v.type)) return false;
  return typeof v.payload === 'object' && v.payload !== null && !Array.isArray(v.payload);
}

/** Ops the copilot is allowed to emit (§10 prompt generation filters on this). */
export function copilotOpSpecs(): OpSpec[] {
  return OP_CATALOG.filter((s) => s.copilot);
}

/**
 * Render the op catalogue as the copilot system-prompt section (§10).
 *
 * This is deliberately terse and unit-explicit: the LLM's whole job is to pick
 * a type and fill integer-mm fields. Generated, never hand-written, so a new op
 * is available to the copilot the moment it lands in OP_CATALOG.
 */
export function renderOpCatalogForPrompt(opts: { copilotOnly?: boolean } = {}): string {
  const specs = opts.copilotOnly === false ? OP_CATALOG : copilotOpSpecs();
  const lines: string[] = [];
  lines.push('# Op catalogue');
  lines.push('');
  lines.push(
    'All lengths are INTEGER MILLIMETRES. Areas are integer mm². Angles are integer degrees.',
  );
  lines.push(
    'Emit ops only from this list. Never emit coordinates you were not given or told to compute.',
  );
  lines.push('');
  for (const spec of specs) {
    lines.push(`## ${spec.type} — ${spec.title}`);
    lines.push(spec.summary);
    if (spec.actions) lines.push(`action: ${spec.actions.join(' | ')}`);
    for (const f of spec.payload) {
      const bits: string[] = [f.type];
      if (f.units) bits.push(f.units);
      if (f.idType) bits.push(`${f.idType}_<ulid>`);
      if (f.enumValues) bits.push(f.enumValues.join('|'));
      if (f.nullable) bits.push('nullable');
      bits.push(f.required ? 'required' : 'optional');
      lines.push(`- ${f.name} (${bits.join(', ')}): ${f.description}`);
    }
    lines.push(`example: ${JSON.stringify(spec.example)}`);
    lines.push('');
  }
  return lines.join('\n');
}
