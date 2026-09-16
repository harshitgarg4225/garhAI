/**
 * material3d.test.ts — the facade's lighting contract, pinned.
 *
 * The scorecard finding this answers: "FacadeLayer uses unlit
 * MeshBasicMaterial with baked shading; chajjas, porches and cladding cast
 * no shadow and ignore the sun scrub". A regression to an unlit material or
 * to `castShadow: false` fails here; the sun-angle pixel evidence is the
 * browser's job (`e2e/tests/three-d.spec.ts`).
 */

import { describe, expect, it } from 'vitest';

import { FrontSide, MeshBasicMaterial, MeshStandardMaterial } from 'three';

import { createFacadeMaterial, FACADE_MESH_SHADOW } from './material3d';

describe('createFacadeMaterial', () => {
  it('is a LIT material with vertex colours — never the unlit basic material', () => {
    const material = createFacadeMaterial();
    expect(material).toBeInstanceOf(MeshStandardMaterial);
    expect(material).not.toBeInstanceOf(MeshBasicMaterial);
    expect(material.vertexColors).toBe(true);
    expect(material.side).toBe(FrontSide);
    material.dispose();
  });

  it('is fresh per call, so a layer can dispose its own without touching another', () => {
    const a = createFacadeMaterial();
    const b = createFacadeMaterial();
    expect(a).not.toBe(b);
    a.dispose();
    b.dispose();
  });
});

describe('FACADE_MESH_SHADOW', () => {
  it('casts AND receives — the elements meant to shade windows must shade', () => {
    expect(FACADE_MESH_SHADOW).toEqual({ castShadow: true, receiveShadow: true });
  });
});
