/**
 * Spec for the `material.assign` builders — folded through the REAL
 * `applyGroup`, because the one invariant that matters here lives in the
 * fold: assignments are keyed by id, so a deterministic id per target means
 * "one document row per target", and a random id would mean a document that
 * grows by one shadowed row per swatch click, forever.
 */

import { describe, expect, it } from 'vitest';

import {
  applyGroup,
  makeTwoRoomPlan,
  stateHash,
  validateOpShape,
  type Op,
  type ProjectDoc,
  type SurfaceGroupRef,
} from '@garh/model';

import { assignmentIdFor, materialAssignOp, materialClearOp, surfaceTargetKey } from './assignOps';

const BUILDING_EXT: SurfaceGroupRef = { group: 'external_wall', storeyId: null, elementId: null };

function fold(doc: ProjectDoc, op: Op): ProjectDoc {
  return applyGroup(doc, [op]).model;
}

describe('materialAssignOp', () => {
  it('emits a shape the server would sequence', () => {
    const doc = makeTwoRoomPlan();
    const op = materialAssignOp(doc.house, BUILDING_EXT, 'exposed-brick');
    expect(validateOpShape(op)).toEqual([]);
  });

  it('one target = one document row, replaced on re-assign — never accumulated', () => {
    let doc = makeTwoRoomPlan();
    doc = fold(doc, materialAssignOp(doc.house, BUILDING_EXT, 'exposed-brick'));
    expect(doc.house.materials).toHaveLength(1);
    const firstId = doc.house.materials[0]?.id;

    doc = fold(doc, materialAssignOp(doc.house, BUILDING_EXT, 'exterior-texture'));
    expect(doc.house.materials).toHaveLength(1);
    expect(doc.house.materials[0]?.id).toBe(firstId);
    expect(doc.house.materials[0]?.materialId).toBe('exterior-texture');
  });

  it('a storey-narrowed target is a different row than the building-wide one', () => {
    let doc = makeTwoRoomPlan();
    const storeyId = doc.house.storeys[0]?.id ?? null;
    expect(storeyId).not.toBeNull();
    const storeyTarget: SurfaceGroupRef = {
      group: 'external_wall',
      storeyId,
      elementId: null,
    };
    doc = fold(doc, materialAssignOp(doc.house, BUILDING_EXT, 'exposed-brick'));
    doc = fold(doc, materialAssignOp(doc.house, storeyTarget, 'stone-cladding'));
    expect(doc.house.materials).toHaveLength(2);
    expect(surfaceTargetKey(BUILDING_EXT)).not.toBe(surfaceTargetKey(storeyTarget));
  });

  it('the derived id is deterministic — two clients agree without talking', () => {
    const doc = makeTwoRoomPlan();
    const a = assignmentIdFor(doc.house, BUILDING_EXT);
    const b = assignmentIdFor(doc.house, BUILDING_EXT);
    expect(a).toBe(b);
    expect(a.startsWith('material_')).toBe(true);
  });

  it('undo restores the document hash exactly (fold inverse round-trip)', () => {
    const doc = makeTwoRoomPlan();
    const before = stateHash(doc);
    const result = applyGroup(doc, [materialAssignOp(doc.house, BUILDING_EXT, 'exposed-brick')]);
    expect(stateHash(result.model)).not.toBe(before);
    const undone = applyGroup(result.model, result.inverse).model;
    expect(stateHash(undone)).toBe(before);
  });
});

describe('materialClearOp', () => {
  it('clears an existing row through the fold', () => {
    let doc = makeTwoRoomPlan();
    doc = fold(doc, materialAssignOp(doc.house, BUILDING_EXT, 'exposed-brick'));
    const clear = materialClearOp(doc.house, BUILDING_EXT);
    expect(clear).not.toBeNull();
    if (clear === null) return;
    doc = fold(doc, clear);
    expect(doc.house.materials).toHaveLength(0);
  });

  it('returns null when there is nothing to clear — no junk undo entries', () => {
    const doc = makeTwoRoomPlan();
    expect(materialClearOp(doc.house, BUILDING_EXT)).toBeNull();
  });
});
