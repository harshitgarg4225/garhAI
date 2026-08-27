/**
 * model.ts — the HouseModel document (playbook §3) and the ProjectDoc that the
 * op log folds into.
 *
 * TWO DOCUMENTS, ON PURPOSE:
 *
 *   `HouseModel` is EXACTLY the §3 shape — storeys, walls, openings, rooms,
 *   stairs, slabs, columns, furniture, facade, materials, levels, balconies,
 *   meta. It is what a design version stores (`design_versions.snapshot`), what
 *   the solver emits, and what the 3D/sheet pipelines consume.
 *
 *   `ProjectDoc` wraps it with the three things ops 1–5 and 32 mutate but which
 *   the DB keeps in their own tables (`plots`, `briefs`, `annotations`):
 *   `{ schemaVersion, plot, brief, house, annotations }`. `fold()` operates on
 *   ProjectDoc because the op log is per-project and contains plot/brief/
 *   annotation ops; a server that folded only HouseModel would have nowhere to
 *   put `plot.set_boundary`. `stateHash` covers the whole ProjectDoc.
 *
 * EVERY LENGTH IS INTEGER MILLIMETRES. Every area is integer mm². Angles are
 * integer degrees. There is no float anywhere in this document — `canonicalJson`
 * throws if one appears, which is how we keep the hash stable across languages.
 */

import type {
  AnnotationId,
  BalconyId,
  ColumnId,
  FacadeComponentId,
  FurnitureId,
  MaterialAssignmentId,
  OpeningId,
  RoomId,
  SheetId,
  SlabId,
  StairId,
  StoreyId,
  WallId,
} from './ids';
import type { Polygon, Pt } from './geometry';
import type { UnitsDisplay } from './units';

// ---------------------------------------------------------------------------
// JSON value types (brief data, reg-profile overrides, annotation payloads)
// ---------------------------------------------------------------------------

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type JsonObject = Record<string, JsonValue>;

/** The document schema version. Bump ⇒ write a migration in `migrate.ts`. */
export const SCHEMA_VERSION = 1;

// ---------------------------------------------------------------------------
// Enums — every one spelled out, no string widening anywhere
// ---------------------------------------------------------------------------

/**
 * Room programme types. Drives NBC minimums, furniture sets, Vastu zones,
 * schedules and labels — so it is a closed list, not free text.
 */
export const ROOM_TYPES = [
  'unassigned',
  'living',
  'dining',
  'living_dining',
  'kitchen',
  'utility',
  'store',
  'bedroom_master',
  'bedroom',
  'guest_bedroom',
  'servant_room',
  'study',
  'pooja',
  'bath',
  'wc',
  'bath_wc',
  'dress',
  'passage',
  'lobby',
  'foyer',
  'staircase',
  'balcony',
  'terrace',
  'porch',
  'garage',
  'stilt',
  'shaft',
  'duct',
  'void',
] as const;
export type RoomType = (typeof ROOM_TYPES)[number];

/** Human labels for chips, room tags and drawing labels. */
export const ROOM_TYPE_LABELS: Readonly<Record<RoomType, string>> = {
  unassigned: 'Room',
  living: 'Living',
  dining: 'Dining',
  living_dining: 'Living / Dining',
  kitchen: 'Kitchen',
  utility: 'Utility',
  store: 'Store',
  bedroom_master: 'Master Bedroom',
  bedroom: 'Bedroom',
  guest_bedroom: 'Guest Bedroom',
  servant_room: 'Servant Room',
  study: 'Study',
  pooja: 'Pooja',
  bath: 'Bath',
  wc: 'W.C.',
  bath_wc: 'Toilet',
  dress: 'Dress',
  passage: 'Passage',
  lobby: 'Lobby',
  foyer: 'Foyer',
  staircase: 'Staircase',
  balcony: 'Balcony',
  terrace: 'Terrace',
  porch: 'Porch',
  garage: 'Garage',
  stilt: 'Stilt',
  shaft: 'Shaft',
  duct: 'Duct',
  void: 'Void',
};

/** NBC "habitable room" set — these carry the 9.5m² / 2.4m width / 1:10 light rules. */
export const HABITABLE_ROOM_TYPES: readonly RoomType[] = [
  'living',
  'dining',
  'living_dining',
  'bedroom_master',
  'bedroom',
  'guest_bedroom',
  'servant_room',
  'study',
];

/** Wet rooms — drive plumbing-stack scoring and shaft adjacency. */
export const WET_ROOM_TYPES: readonly RoomType[] = ['kitchen', 'bath', 'wc', 'bath_wc', 'utility'];

export function isHabitableRoomType(t: RoomType): boolean {
  return HABITABLE_ROOM_TYPES.includes(t);
}
export function isWetRoomType(t: RoomType): boolean {
  return WET_ROOM_TYPES.includes(t);
}

export const WALL_KINDS = ['external', 'internal', 'parapet'] as const;
export type WallKind = (typeof WALL_KINDS)[number];

export const OPENING_KINDS = ['door', 'window', 'ventilator'] as const;
export type OpeningKind = (typeof OPENING_KINDS)[number];

/** §3, verbatim. Sliding/fixed leaves are a v1.1 concern. */
export const OPENING_SWINGS = ['in-left', 'in-right', 'out-left', 'out-right'] as const;
export type OpeningSwing = (typeof OPENING_SWINGS)[number];

export const STAIR_KINDS = ['straight', 'dogleg', 'L', 'U'] as const;
export type StairKind = (typeof STAIR_KINDS)[number];

/** Orthogonal travel direction. MVP walls and stairs are orthogonal (§7). */
export const DIRECTIONS_4 = ['N', 'E', 'S', 'W'] as const;
export type Direction4 = (typeof DIRECTIONS_4)[number];

/** 8-way compass, used for facing/Vastu zones and elevation naming. */
export const DIRECTIONS_8 = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'] as const;
export type Direction8 = (typeof DIRECTIONS_8)[number];

export const SLAB_KINDS = ['floor', 'terrace', 'plinth', 'mumty'] as const;
export type SlabKind = (typeof SLAB_KINDS)[number];

export const RAILING_KINDS = ['ms', 'glass', 'masonry', 'ms_glass', 'none'] as const;
export type RailingKind = (typeof RAILING_KINDS)[number];

export const FACADE_COMPONENT_KINDS = [
  'window_trim',
  'chajja',
  'parapet_profile',
  'cladding_zone',
  'porch',
  'railing',
  'band',
  'louver',
  'entry_feature',
] as const;
export type FacadeComponentKind = (typeof FACADE_COMPONENT_KINDS)[number];

/** Surface groups a material can be assigned to (op 29). */
export const SURFACE_GROUPS = [
  'external_wall',
  'internal_wall',
  'floor',
  'ceiling',
  'roof',
  'parapet',
  'railing',
  'door',
  'window',
  'cladding',
  'plinth',
  'staircase',
] as const;
export type SurfaceGroup = (typeof SURFACE_GROUPS)[number];

export const VASTU_MODES = ['off', 'advisory', 'strict'] as const;
export type VastuMode = (typeof VASTU_MODES)[number];

/** Where an op came from — mirrors `ops.source` in the DDL. */
export const OP_SOURCES = ['manual', 'copilot', 'solver', 'system'] as const;
export type OpSource = (typeof OP_SOURCES)[number];

/** What a sheet annotation is anchored to (§7 annotation anchoring). */
export const ANNOTATION_ANCHOR_KINDS = [
  'wall',
  'opening',
  'room',
  'stair',
  'column',
  'balcony',
  'sheet',
] as const;
export type AnnotationAnchorKind = (typeof ANNOTATION_ANCHOR_KINDS)[number];

// ---------------------------------------------------------------------------
// Element interfaces
// ---------------------------------------------------------------------------

/** Rectangular size in mm (columns, landings, catalogue footprints). */
export interface SizeMm {
  readonly xMm: number;
  readonly yMm: number;
}

/**
 * Per-storey level data. First-class because sections and compliance consume it
 * (§3): a section draws FFL, sill and lintel lines straight off these numbers.
 */
export interface LevelData {
  /** Finished floor level of this storey, measured from plot datum (0). */
  readonly fflMm: number;
  /** Structural slab thickness under this storey's FFL. */
  readonly slabThicknessMm: number;
  /** Storey-level override of `Levels.sillDefaultMm`, or null to inherit. */
  readonly sillDefaultMm: number | null;
  /** Storey-level override of `Levels.lintelDefaultMm`, or null to inherit. */
  readonly lintelDefaultMm: number | null;
}

export interface Storey {
  readonly id: StoreyId;
  /** Display name: "Ground Floor", "First Floor", "Terrace". */
  readonly name: string;
  readonly level: LevelData;
  /** Floor-to-floor height in mm. */
  readonly heightMm: number;
}

export interface Wall {
  readonly id: WallId;
  readonly storeyId: StoreyId;
  /** Centreline start. */
  readonly a: Pt;
  /** Centreline end. */
  readonly b: Pt;
  /** 115 / 150 / 200 / 230 / custom, always integer mm. */
  readonly thicknessMm: number;
  readonly kind: WallKind;
  /** Coordination hint for the structural note; not used for geometry. */
  readonly loadBearing: boolean;
}

export interface Opening {
  readonly id: OpeningId;
  readonly wallId: WallId;
  readonly kind: OpeningKind;
  readonly widthMm: number;
  readonly heightMm: number;
  /** Height of the sill above this storey's FFL. Doors are 0. */
  readonly sillMm: number;
  /** Distance along the host wall from `wall.a` to the opening CENTRE. */
  readonly offsetMm: number;
  readonly swing: OpeningSwing;
  /** Schedule tag: D1, W2, V1… assigned by the schedule generator (§7). */
  readonly tag: string | null;
}

export interface Room {
  readonly id: RoomId;
  readonly storeyId: StoreyId;
  readonly type: RoomType;
  /** Empty string until the user or solver names it; UI falls back to the type label. */
  readonly name: string;
  /** Clear (inside-face) polygon, CCW, integer mm. */
  readonly polygon: Polygon;
  /** Clear floor area of `polygon`, integer mm². */
  readonly areaMm2: number;
  readonly tags: readonly string[];
  /** True ⇒ solver partial re-solve must return this room untouched (§5.7). */
  readonly locked: boolean;
  /** Brief/solver target area (op 20), or null. */
  readonly targetAreaMm2: number | null;
  /** Required facing (op 20), or null. */
  readonly mustFace: Direction8 | null;
}

/** Landing block of a stair, or null for a single straight flight. */
export interface StairLanding {
  readonly widthMm: number;
  readonly depthMm: number;
}

export interface Stair {
  readonly id: StairId;
  readonly storeyId: StoreyId;
  readonly kind: StairKind;
  /** Bottom-left corner of the stair footprint (first riser, going `direction`). */
  readonly origin: Pt;
  /** Direction of travel going UP. */
  readonly direction: Direction4;
  readonly riserMm: number;
  readonly treadMm: number;
  /** Clear flight width. */
  readonly widthMm: number;
  /** risersCount × riserMm ≈ storey height (±10mm invariant). */
  readonly risersCount: number;
  readonly landing: StairLanding | null;
}

export interface Slab {
  readonly id: SlabId;
  readonly storeyId: StoreyId;
  readonly kind: SlabKind;
  /** Outer boundary, CCW. */
  readonly polygon: Polygon;
  readonly thicknessMm: number;
  /** Stair wells, double-height voids, shafts. */
  readonly cutouts: readonly Polygon[];
}

export interface Column {
  readonly id: ColumnId;
  readonly storeyId: StoreyId;
  /** Centre of the column. */
  readonly pt: Pt;
  readonly sizeMm: SizeMm;
}

export interface FurnitureInstance {
  readonly id: FurnitureId;
  readonly storeyId: StoreyId;
  /** Key into the furniture catalogue (`GET /catalog/furniture`). */
  readonly catalogId: string;
  /** Centre of the footprint. */
  readonly pt: Pt;
  /** Integer degrees CCW; 0 = catalogue default orientation. */
  readonly rotationDeg: number;
}

export interface Balcony {
  readonly id: BalconyId;
  readonly storeyId: StoreyId;
  readonly polygon: Polygon;
  readonly railingKind: RailingKind;
  readonly railingHeightMm: number;
  /** Projection beyond the building line — checked against projection rules. */
  readonly projectionMm: number;
  readonly slabThicknessMm: number;
}

/**
 * Facade sub-model. ISOLATED BY DESIGN (§3, §8): nothing in here may affect
 * walls, rooms, openings or areas, so facade churn can never break the drawing
 * set or a compliance number.
 */
export interface FacadeModel {
  /** Kit id, or null when no kit has been applied. */
  readonly kitId: string | null;
  /** Variation seed for the generator (integer). */
  readonly seed: number;
  readonly colorwayId: string | null;
  readonly components: readonly FacadeComponent[];
}

export interface FacadeComponent {
  readonly id: FacadeComponentId;
  readonly kind: FacadeComponentKind;
  readonly storeyId: StoreyId | null;
  readonly wallId: WallId | null;
  readonly openingId: OpeningId | null;
  /** Generator parameters. Integers only for lengths (projectionMm etc.). */
  readonly params: JsonObject;
}

export interface SurfaceGroupRef {
  readonly group: SurfaceGroup;
  /** Narrow the assignment to one storey, or null for the whole building. */
  readonly storeyId: StoreyId | null;
  /** Narrow to a single element (wall/opening/facade component), or null. */
  readonly elementId: string | null;
}

export interface MaterialAssignment {
  readonly id: MaterialAssignmentId;
  readonly target: SurfaceGroupRef;
  /** Key into the material catalogue. */
  readonly materialId: string;
}

/**
 * Building-wide levels. First-class (§3) because sections, elevations and the
 * ventilation/height rules all read them.
 */
export interface Levels {
  /** Plinth height above ground level. */
  readonly plinthMm: number;
  /** FFL of each storey, index-aligned with `storeys`. */
  readonly fflPerStoreyMm: readonly number[];
  /** Default window sill height (NBC/city packs expect 900). */
  readonly sillDefaultMm: number;
  /** Default lintel height above FFL (2100 typical). */
  readonly lintelDefaultMm: number;
  /** Terrace parapet height (1000 typical, city packs may raise it). */
  readonly parapetMm: number;
}

export interface ModelMeta {
  readonly unitsDisplay: UnitsDisplay;
  /** Reference to the regulatory profile in use (`plots.reg_profile`), or null. */
  readonly regProfileRef: string | null;
  /** Reference to the brief this model was generated from, or null. */
  readonly briefRef: string | null;
}

/**
 * The §3 house document. Arrays are kept in a canonical order (see
 * `sortModelArrays` in fold.ts) so that two folds of the same op log serialise
 * identically.
 */
export interface HouseModel {
  readonly schemaVersion: number;
  /** Ordered, ground floor = index 0. */
  readonly storeys: readonly Storey[];
  readonly walls: readonly Wall[];
  readonly openings: readonly Opening[];
  /** DERIVED from walls by planar subdivision, but persisted with stable ids. */
  readonly rooms: readonly Room[];
  readonly stairs: readonly Stair[];
  /** DERIVED per storey. */
  readonly slabs: readonly Slab[];
  readonly columns: readonly Column[];
  readonly furniture: readonly FurnitureInstance[];
  readonly facade: FacadeModel;
  readonly materials: readonly MaterialAssignment[];
  readonly levels: Levels;
  readonly balconies: readonly Balcony[];
  readonly meta: ModelMeta;
}

// ---------------------------------------------------------------------------
// Plot / brief / annotations — the rest of the folded project document
// ---------------------------------------------------------------------------

/** A road on one edge of the plot boundary (drives setback tables). */
export interface Road {
  /** Index of the boundary edge `boundary[i] -> boundary[i+1]`. */
  readonly edgeIndex: number;
  /** Road width in mm, or null for "no road on this edge". */
  readonly widthMm: number | null;
  readonly name: string | null;
}

/** The regulatory profile: a city pack plus per-project overrides. */
export interface RegProfile {
  /** Rule pack id: 'blr' | 'ncr' | 'hyd' | … (packs live in `rulepacks/`). */
  readonly cityPack: string | null;
  /** Per-project overrides (logged in audit_log — §13). */
  readonly overrides: JsonObject;
}

export interface PlotDoc {
  /** Plot boundary, CCW, plot-local mm, origin at the SW corner. */
  readonly boundary: Polygon;
  /** Integer degrees: rotation of TRUE north from +Y, measured clockwise. */
  readonly northDeg: number;
  readonly roads: readonly Road[];
  readonly regProfile: RegProfile;
  /** How the boundary got here: 'manual' | 'dxf' | 'seed'. */
  readonly source: string;
}

export interface BriefDoc {
  /** Free-form brief data; the shape is owned by the brief schema, not by geometry. */
  readonly data: JsonObject;
  readonly vastuMode: VastuMode;
  /** 0–100 completeness meter (§F2). */
  readonly completeness: number;
}

/** A sheet annotation anchored to a model element (§7). */
export interface Annotation {
  readonly id: AnnotationId;
  readonly sheetId: SheetId;
  readonly anchorElementId: string | null;
  readonly anchorKind: AnnotationAnchorKind;
  readonly payload: JsonObject;
  /** True after a solver re-run destroyed the anchor → Review Tray (§7). */
  readonly orphaned: boolean;
}

/**
 * THE FOLDED DOCUMENT. `fold(model, op)` takes and returns this.
 * `stateHash(doc)` hashes exactly this.
 */
export interface ProjectDoc {
  readonly schemaVersion: number;
  readonly plot: PlotDoc;
  readonly brief: BriefDoc;
  readonly house: HouseModel;
  readonly annotations: readonly Annotation[];
}

/** Alias for call sites that read better as "the model". */
export type Model = ProjectDoc;

// ---------------------------------------------------------------------------
// Defaults / constructors
// ---------------------------------------------------------------------------

/** Indian residential defaults, all integer mm. Cited in assumption chips. */
export const DEFAULTS = {
  storeyHeightMm: 3000,
  slabThicknessMm: 150,
  plinthMm: 600,
  sillDefaultMm: 900,
  lintelDefaultMm: 2100,
  parapetMm: 1000,
  externalWallThicknessMm: 230,
  internalWallThicknessMm: 115,
  parapetThicknessMm: 115,
  doorWidthMm: 900,
  doorHeightMm: 2100,
  bathDoorWidthMm: 750,
  windowWidthMm: 1200,
  windowHeightMm: 1200,
  ventilatorWidthMm: 600,
  ventilatorHeightMm: 450,
  ventilatorSillMm: 1800,
  riserMm: 165,
  treadMm: 275,
  stairWidthMm: 900,
  railingHeightMm: 1000,
  balconyProjectionMm: 900,
  columnSizeMm: { xMm: 230, yMm: 230 } as SizeMm,
} as const;

export function emptyLevels(): Levels {
  return {
    plinthMm: DEFAULTS.plinthMm,
    fflPerStoreyMm: [],
    sillDefaultMm: DEFAULTS.sillDefaultMm,
    lintelDefaultMm: DEFAULTS.lintelDefaultMm,
    parapetMm: DEFAULTS.parapetMm,
  };
}

export function emptyFacade(): FacadeModel {
  return { kitId: null, seed: 0, colorwayId: null, components: [] };
}

export function emptyHouseModel(unitsDisplay: UnitsDisplay = 'ft-in'): HouseModel {
  return {
    schemaVersion: SCHEMA_VERSION,
    storeys: [],
    walls: [],
    openings: [],
    rooms: [],
    stairs: [],
    slabs: [],
    columns: [],
    furniture: [],
    facade: emptyFacade(),
    materials: [],
    levels: emptyLevels(),
    balconies: [],
    meta: { unitsDisplay, regProfileRef: null, briefRef: null },
  };
}

export function emptyPlot(): PlotDoc {
  return {
    boundary: [],
    northDeg: 0,
    roads: [],
    regProfile: { cityPack: null, overrides: {} },
    source: 'manual',
  };
}

export function emptyBrief(): BriefDoc {
  return { data: {}, vastuMode: 'off', completeness: 0 };
}

/** The initial state every op log folds from. */
export function emptyProjectDoc(unitsDisplay: UnitsDisplay = 'ft-in'): ProjectDoc {
  return {
    schemaVersion: SCHEMA_VERSION,
    plot: emptyPlot(),
    brief: emptyBrief(),
    house: emptyHouseModel(unitsDisplay),
    annotations: [],
  };
}

/** Default level data for a storey at `fflMm`. */
export function defaultLevelData(fflMm: number): LevelData {
  return {
    fflMm,
    slabThicknessMm: DEFAULTS.slabThicknessMm,
    sillDefaultMm: null,
    lintelDefaultMm: null,
  };
}

// ---------------------------------------------------------------------------
// Lookups & derived reads (pure, no mutation)
// ---------------------------------------------------------------------------

export function findStorey(house: HouseModel, storeyId: StoreyId): Storey | undefined {
  return house.storeys.find((s) => s.id === storeyId);
}
export function storeyIndex(house: HouseModel, storeyId: StoreyId): number {
  return house.storeys.findIndex((s) => s.id === storeyId);
}
export function findWall(house: HouseModel, wallId: WallId): Wall | undefined {
  return house.walls.find((w) => w.id === wallId);
}
export function findOpening(house: HouseModel, openingId: OpeningId): Opening | undefined {
  return house.openings.find((o) => o.id === openingId);
}
export function findRoom(house: HouseModel, roomId: RoomId): Room | undefined {
  return house.rooms.find((r) => r.id === roomId);
}
export function findStair(house: HouseModel, stairId: StairId): Stair | undefined {
  return house.stairs.find((s) => s.id === stairId);
}
export function wallsOfStorey(house: HouseModel, storeyId: StoreyId): Wall[] {
  return house.walls.filter((w) => w.storeyId === storeyId);
}
export function roomsOfStorey(house: HouseModel, storeyId: StoreyId): Room[] {
  return house.rooms.filter((r) => r.storeyId === storeyId);
}
export function openingsOfWall(house: HouseModel, wallId: WallId): Opening[] {
  return house.openings.filter((o) => o.wallId === wallId);
}

/**
 * Effective sill default for a storey: storey override, else building default.
 */
export function effectiveSillMm(house: HouseModel, storeyId: StoreyId): number {
  const storey = findStorey(house, storeyId);
  return storey?.level.sillDefaultMm ?? house.levels.sillDefaultMm;
}

/** Effective lintel height for a storey. */
export function effectiveLintelMm(house: HouseModel, storeyId: StoreyId): number {
  const storey = findStorey(house, storeyId);
  return storey?.level.lintelDefaultMm ?? house.levels.lintelDefaultMm;
}

/** Sum of storey heights + plinth — the height a `height_max` rule checks. */
export function buildingHeightMm(house: HouseModel): number {
  let total = house.levels.plinthMm;
  for (const s of house.storeys) total += s.heightMm;
  return total;
}

/** Total built-up area = Σ slab areas (mm²). Used by the area statement. */
export function builtUpAreaMm2(house: HouseModel, areaOf: (p: Polygon) => number): number {
  let total = 0;
  for (const slab of house.slabs) {
    if (slab.kind !== 'floor') continue;
    total += areaOf(slab.polygon);
    for (const cut of slab.cutouts) total -= areaOf(cut);
  }
  return total;
}

/** Display name for a room: explicit name, else type label, else "Room N". */
export function roomDisplayName(room: Room, ordinal?: number): string {
  if (room.name !== '') return room.name;
  const label = ROOM_TYPE_LABELS[room.type];
  if (room.type === 'unassigned' && ordinal !== undefined) return `${label} ${String(ordinal)}`;
  return label;
}
