/**
 * mm → canvas pixels, through a real camera.
 *
 * The overlay's whole claim is that a pin sits on the millimetre it was placed
 * on. That claim rests on two sign conventions the drawing already depends on —
 * north is −Z in world space, and screen Y grows downwards — and getting either
 * backwards mirrors or flips every mark while still producing plausible-looking
 * numbers. `coords.test.ts` asserts those signs for the model; this asserts that
 * composing them for the overlay preserves them.
 *
 * The camera here is built the way `CameraRig` builds the 2D one: orthographic,
 * looking straight down, `up` = (0, 0, −1) so north reads upwards on screen.
 */

import { OrthographicCamera } from 'three';
import { describe, expect, it } from 'vitest';

import { WORLD_UNITS_PER_MM } from '../core';
import { projectMmToOverlay } from './overlayProjection';

const SIZE = { width: 800, height: 600 };

/** A top-down ortho camera centred on the plot origin, `mmPerPx` mm per pixel. */
function planCamera(mmPerPx: number): OrthographicCamera {
  const halfWidthWorld = (SIZE.width / 2) * mmPerPx * WORLD_UNITS_PER_MM;
  const halfHeightWorld = (SIZE.height / 2) * mmPerPx * WORLD_UNITS_PER_MM;
  const camera = new OrthographicCamera(
    -halfWidthWorld,
    halfWidthWorld,
    halfHeightWorld,
    -halfHeightWorld,
    0.01,
    400,
  );
  camera.position.set(0, 100, 0);
  camera.up.set(0, 0, -1);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld(true);
  camera.updateProjectionMatrix();
  return camera;
}

describe('projectMmToOverlay', () => {
  it('puts the plot origin at the centre of the canvas', () => {
    const point = projectMmToOverlay({ x: 0, y: 0 }, 0, planCamera(10), SIZE);
    expect(point.x).toBeCloseTo(400, 6);
    expect(point.y).toBeCloseTo(300, 6);
    expect(point.onScreen).toBe(true);
  });

  it('east is right and NORTH IS UP', () => {
    // The sign that mirrors every plan if it is wrong. Model +Y is north, world
    // −Z, screen −Y: a point 1000mm north of the origin must be ABOVE centre.
    const camera = planCamera(10);
    const east = projectMmToOverlay({ x: 1000, y: 0 }, 0, camera, SIZE);
    const north = projectMmToOverlay({ x: 0, y: 1000 }, 0, camera, SIZE);

    expect(east.x).toBeCloseTo(500, 6); // 1000mm ÷ 10mm/px = 100px right
    expect(east.y).toBeCloseTo(300, 6);
    expect(north.y).toBeCloseTo(200, 6); // 100px UP the screen
    expect(north.x).toBeCloseTo(400, 6);
  });

  it('scales with the zoom', () => {
    const far = projectMmToOverlay({ x: 1000, y: 0 }, 0, planCamera(20), SIZE);
    expect(far.x).toBeCloseTo(450, 6); // half as many pixels at twice the mm/px
  });

  it('reports a point off the canvas as not on screen', () => {
    // 8000mm at 10mm/px is 800px right of centre — well past the edge.
    const point = projectMmToOverlay({ x: 80_000, y: 0 }, 0, planCamera(10), SIZE);
    expect(point.onScreen).toBe(false);
  });

  it('honours the margin so a mark slides off rather than blinking off', () => {
    const camera = planCamera(10);
    // 4100mm east = 410px right of centre = x 810, ten pixels past the edge.
    const strict = projectMmToOverlay({ x: 4100, y: 0 }, 0, camera, SIZE);
    const lenient = projectMmToOverlay({ x: 4100, y: 0 }, 0, camera, SIZE, 48);
    expect(strict.onScreen).toBe(false);
    expect(lenient.onScreen).toBe(true);
    // Same pixel either way — the margin changes the verdict, not the maths.
    expect(lenient.x).toBeCloseTo(strict.x, 6);
  });

  it('rejects a point beyond the far clip plane whatever its pixel', () => {
    // Depth is not subject to the margin: a point behind a perspective camera
    // projects to a plausible-looking pixel and must stay rejected.
    const camera = planCamera(10);
    // Camera sits at y=100 world (100m) with `far` 400; 500m below the datum is
    // past it while still projecting to the centre of the canvas.
    const point = projectMmToOverlay({ x: 0, y: 0 }, -500_000, camera, SIZE, 10_000);
    expect(point.onScreen).toBe(false);
  });

  it('allocates nothing that escapes — repeated calls stay independent', () => {
    // The module reuses one scratch Vector3; a returned reference to it would
    // make every mark share the last one's position.
    const camera = planCamera(10);
    const a = projectMmToOverlay({ x: 1000, y: 0 }, 0, camera, SIZE);
    const b = projectMmToOverlay({ x: -1000, y: 0 }, 0, camera, SIZE);
    expect(a.x).toBeCloseTo(500, 6);
    expect(b.x).toBeCloseTo(300, 6);
  });
});
