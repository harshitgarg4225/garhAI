/**
 * cameras.ts — where each render preset stands and what it looks at.
 *
 * §9's camera presets: `exterior-street-day`, `exterior-34*` (three-quarter),
 * dusk/night variants, plus `interior-living` / `interior-kitchen` pointed at
 * the relevant rooms — room centroids come from the model document, never from
 * an LLM (locked decision: LLMs never emit geometry; neither does this file
 * emit any into the model — these are viewing positions only).
 *
 * All maths is done in integer-mm model space, converted to the canvas world
 * mapping (`worldX = +mmX·0.001, worldY = +elev·0.001, worldZ = −mmY·0.001`)
 * only at the very end via the core's own `mmToWorldXYZ` — the one mapping the
 * whole scene uses, so a preset camera sees exactly what the user's does.
 */

import { PerspectiveCamera, Vector3 } from 'three';

import { polygonCentroid, type HouseModel, type Pt, type Room } from '@garh/model';

import { mmToWorldXYZ } from '../../features/canvas/core';
import { PRESETS_BY_ID } from './presets';

export interface PresetView {
  readonly camera: PerspectiveCamera;
  /** Integer-mm camera state, stored on the job row (`RenderIn.view`). */
  readonly viewMeta: {
    readonly preset: string;
    readonly eyeMm: { x: number; y: number; z: number };
    readonly targetMm: { x: number; y: number; z: number };
    readonly fovDeg: number;
  };
}

/** Why a preset camera could not be placed — shown to the user verbatim. */
export class PresetCameraError extends Error {}

interface BboxMm {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
  heightMm: number;
}

const EYE_LEVEL_MM = 1700;
const INTERIOR_EYE_MM = 1500;
const INTERIOR_TARGET_MM = 1100;
const EXTERIOR_FOV_DEG = 45;
const INTERIOR_FOV_DEG = 60;

/**
 * The preset's camera over the current model. Throws {@link PresetCameraError}
 * with a sentence the UI can show when the model has nothing to photograph.
 */
export function presetCamera(presetId: string, house: HouseModel): PresetView {
  const preset = PRESETS_BY_ID.get(presetId);
  if (preset === undefined) {
    throw new PresetCameraError(`Unknown render style "${presetId}".`);
  }
  if (preset.scene === 'interior') {
    const room = findRoom(house, preset.roomType);
    if (room === null) {
      throw new PresetCameraError(
        `There is no ${preset.roomType ?? 'matching'} room to photograph yet — name a room "${preset.roomType}" on the plan first.`,
      );
    }
    return interiorView(presetId, room);
  }
  const bbox = buildingBboxMm(house);
  if (bbox === null) {
    throw new PresetCameraError('There are no walls to photograph yet — draw the plan first.');
  }
  return exteriorView(presetId, bbox);
}

// ---------------------------------------------------------------------------
// Exteriors
// ---------------------------------------------------------------------------

function exteriorView(presetId: string, bbox: BboxMm): PresetView {
  const cx = (bbox.minX + bbox.maxX) / 2;
  const cy = (bbox.minY + bbox.maxY) / 2;
  const spanX = bbox.maxX - bbox.minX;
  const spanY = bbox.maxY - bbox.minY;
  const halfFov = (EXTERIOR_FOV_DEG / 2) * (Math.PI / 180);

  let eyeMm: { x: number; y: number; z: number };
  let targetMm: { x: number; y: number; z: number };

  if (presetId === 'exterior-street-day') {
    // From the street: model +Y is north, so the front/road face is south
    // (−Y). Pedestrian eye height, far enough back to frame width AND height.
    const halfSpan = Math.max(spanX, bbox.heightMm) / 2;
    const distance = Math.max(halfSpan / Math.tan(halfFov), 6000) * 1.15;
    eyeMm = { x: cx, y: bbox.minY - distance, z: EYE_LEVEL_MM };
    targetMm = { x: cx, y: cy, z: Math.round(bbox.heightMm * 0.45) };
  } else {
    // Three-quarter (34): south-east, elevated — the classic hero angle.
    // Dusk / night are the same station point; the preset changes the light,
    // not the geometry (that is the whole Precise contract).
    const diagonal = Math.hypot(spanX, spanY);
    const halfSpan = Math.max(diagonal, bbox.heightMm) / 2;
    const distance = Math.max(halfSpan / Math.tan(halfFov), 8000) * 1.1;
    const inv = Math.SQRT1_2; // unit (1, −1) direction: east + south
    eyeMm = {
      x: Math.round(cx + distance * inv),
      y: Math.round(cy - distance * inv),
      z: Math.round(bbox.heightMm * 0.9 + 2500),
    };
    targetMm = { x: cx, y: cy, z: Math.round(bbox.heightMm * 0.4) };
  }
  return buildView(presetId, eyeMm, targetMm, EXTERIOR_FOV_DEG);
}

// ---------------------------------------------------------------------------
// Interiors — aimed at the room's centroid, from inside the room
// ---------------------------------------------------------------------------

function interiorView(presetId: string, room: Room): PresetView {
  // The model core's own centroid (shoelace, integer mm) — never re-derived here.
  const centroid = polygonCentroid(room.polygon);
  // Stand 70% of the way from the centroid to its farthest corner: inside the
  // room for any sensibly-shaped plan, with most of the room in frame.
  let farthest: Pt = centroid;
  let best = 0;
  for (const pt of room.polygon as readonly Pt[]) {
    const d = Math.hypot(pt.x - centroid.x, pt.y - centroid.y);
    if (d > best) {
      best = d;
      farthest = pt;
    }
  }
  const eyeMm = {
    x: Math.round(centroid.x + (farthest.x - centroid.x) * 0.7),
    y: Math.round(centroid.y + (farthest.y - centroid.y) * 0.7),
    z: INTERIOR_EYE_MM,
  };
  const targetMm = { x: centroid.x, y: centroid.y, z: INTERIOR_TARGET_MM };
  return buildView(presetId, eyeMm, targetMm, INTERIOR_FOV_DEG);
}

function findRoom(house: HouseModel, roomType: string | null): Room | null {
  if (roomType === null) return null;
  const groundId = house.storeys[0]?.id ?? null;
  const matches = house.rooms.filter(
    (room) =>
      room.type === roomType ||
      (roomType === 'living' && room.type === 'living_dining') ||
      room.name.toLowerCase().includes(roomType),
  );
  if (matches.length === 0) return null;
  // Prefer the ground floor (that is where clients are shown around), then size.
  const sorted = matches
    .slice()
    .sort(
      (a, b) =>
        Number(b.storeyId === groundId) - Number(a.storeyId === groundId) ||
        b.areaMm2 - a.areaMm2,
    );
  return sorted[0] ?? null;
}

// ---------------------------------------------------------------------------
// Shared maths
// ---------------------------------------------------------------------------

function buildView(
  presetId: string,
  eyeMm: { x: number; y: number; z: number },
  targetMm: { x: number; y: number; z: number },
  fovDeg: number,
): PresetView {
  const camera = new PerspectiveCamera(fovDeg, 3 / 2, 0.1, 500);
  camera.up.set(0, 1, 0);
  const eye = mmToWorldXYZ(eyeMm.x, eyeMm.y, eyeMm.z, new Vector3()) as Vector3;
  const target = mmToWorldXYZ(targetMm.x, targetMm.y, targetMm.z, new Vector3()) as Vector3;
  camera.position.copy(eye);
  camera.lookAt(target);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld();
  return {
    camera,
    viewMeta: {
      preset: presetId,
      eyeMm: { x: Math.round(eyeMm.x), y: Math.round(eyeMm.y), z: Math.round(eyeMm.z) },
      targetMm: {
        x: Math.round(targetMm.x),
        y: Math.round(targetMm.y),
        z: Math.round(targetMm.z),
      },
      fovDeg,
    },
  };
}

/** Bounding box of every wall on every storey, plus the building's top. */
export function buildingBboxMm(house: HouseModel): BboxMm | null {
  if (house.walls.length === 0) return null;
  let minX = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  for (const wall of house.walls) {
    for (const pt of [wall.a, wall.b]) {
      if (pt.x < minX) minX = pt.x;
      if (pt.x > maxX) maxX = pt.x;
      if (pt.y < minY) minY = pt.y;
      if (pt.y > maxY) maxY = pt.y;
    }
  }
  let heightMm = 0;
  for (const storey of house.storeys) {
    heightMm = Math.max(heightMm, storey.level.fflMm + storey.heightMm);
  }
  return { minX, maxX, minY, maxY, heightMm: Math.max(heightMm, 3000) };
}
