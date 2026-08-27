/**
 * buildingBbox.ts — the box the 3D camera fits and the sun's shadow camera
 * frames. Pure over `HouseModel`; no three, no React.
 *
 * Walls contribute their centreline endpoints inflated by half their
 * thickness; slabs, balconies and rooms contribute their polygons; columns
 * their footprints; stairs a conservative footprint from the flight
 * parameters (straight run + landing — honest to the model, which stores no
 * turn geometry; see the Phase-4 stairs note). Height is
 * `buildingHeightMm` + parapet, so a fit shows the terrace wall too.
 */

import { buildingHeightMm, type Bbox, type HouseModel, type Pt, type Stair } from '@garh/model';

export interface BuildingExtent {
  readonly box: Bbox;
  readonly heightMm: number;
}

interface Acc {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
  any: boolean;
}

function addPt(acc: Acc, p: Pt, padMm = 0): void {
  if (p.x - padMm < acc.minX) acc.minX = p.x - padMm;
  if (p.y - padMm < acc.minY) acc.minY = p.y - padMm;
  if (p.x + padMm > acc.maxX) acc.maxX = p.x + padMm;
  if (p.y + padMm > acc.maxY) acc.maxY = p.y + padMm;
  acc.any = true;
}

/** Conservative axis-aligned footprint of a stair (run + landing). */
function stairFootprintPts(stair: Stair): Pt[] {
  const runMm = stair.treadMm * Math.max(0, stair.risersCount - 1);
  const landW = stair.landing?.widthMm ?? 0;
  const landD = stair.landing?.depthMm ?? 0;
  const along = runMm + landD;
  const across = Math.max(stair.widthMm, landW);
  const o = stair.origin;
  switch (stair.direction) {
    case 'N':
      return [o, { x: o.x + across, y: o.y + along }];
    case 'S':
      return [o, { x: o.x + across, y: o.y - along }];
    case 'E':
      return [o, { x: o.x + along, y: o.y + across }];
    case 'W':
      return [o, { x: o.x - along, y: o.y + across }];
  }
}

/**
 * The whole building's plan-space bbox + height, or null when the model has
 * nothing to fit — callers must show a teach-state, not fit a fiction.
 */
export function buildingExtentOf(house: HouseModel): BuildingExtent | null {
  const acc: Acc = { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity, any: false };

  for (const wall of house.walls) {
    const half = Math.ceil(wall.thicknessMm / 2);
    addPt(acc, wall.a, half);
    addPt(acc, wall.b, half);
  }
  for (const slab of house.slabs) for (const p of slab.polygon) addPt(acc, p);
  for (const balcony of house.balconies) for (const p of balcony.polygon) addPt(acc, p);
  for (const room of house.rooms) for (const p of room.polygon) addPt(acc, p);
  for (const column of house.columns) {
    addPt(acc, column.pt, Math.ceil(Math.max(column.sizeMm.xMm, column.sizeMm.yMm) / 2));
  }
  for (const stair of house.stairs) for (const p of stairFootprintPts(stair)) addPt(acc, p);

  if (!acc.any) return null;
  return {
    box: { minX: acc.minX, minY: acc.minY, maxX: acc.maxX, maxY: acc.maxY },
    heightMm: buildingHeightMm(house) + house.levels.parapetMm,
  };
}
