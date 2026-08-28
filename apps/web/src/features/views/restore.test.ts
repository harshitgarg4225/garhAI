/**
 * Spec for the flight, the interruption rule, and the cross-projection
 * decision.
 *
 * The clock is injected so a flight can be driven frame by frame and asserted
 * on exactly, rather than slept through. The VIEWPORT is real: every assertion
 * reads the camera back out of `ViewportController` after the write, so a
 * change that made the flight write to the wrong half — or to nothing at
 * all — fails here rather than in someone's browser.
 */

import { describe, expect, it, vi } from 'vitest';

import { ViewportController } from '../canvas/core/viewport';
import { captureCamera, sameCamera } from './camera';
import { prefersReducedMotion, restoreCamera, RESTORE_DURATION_MS } from './restore';
import type { Saved2dCamera, Saved3dCamera, SavedCamera } from './types';

// ---------------------------------------------------------------------------
// A clock the spec drives by hand
// ---------------------------------------------------------------------------

interface FakeClock {
  clock: { now: () => number; request: (cb: () => void) => number; cancel: (h: number) => void };
  advance: (ms: number) => void;
  /** Run every callback queued for the next frame. */
  frame: () => void;
  pending: () => number;
}

function fakeClock(): FakeClock {
  let time = 0;
  let nextHandle = 1;
  const queued = new Map<number, () => void>();
  return {
    clock: {
      now: () => time,
      request: (callback) => {
        const handle = nextHandle++;
        queued.set(handle, callback);
        return handle;
      },
      cancel: (handle) => {
        queued.delete(handle);
      },
    },
    advance: (ms) => {
      time += ms;
    },
    frame: () => {
      const due = [...queued.values()];
      queued.clear();
      for (const callback of due) callback();
    },
    pending: () => queued.size,
  };
}

function plan(centreX: number, centreY: number, mmPerPx: number): Saved2dCamera {
  return { mode: '2d', centreMm: { x: centreX, y: centreY }, mmPerPx };
}

const ORBIT: Saved3dCamera = {
  mode: '3d',
  targetMm: { x: 4000, y: 2500, z: 1500 },
  distanceMm: 18_733.61,
  azimuthDeg: 217.37,
  polarDeg: 63.19,
};

function viewport(mode: '2d' | '3d' = '2d'): ViewportController {
  const controller = new ViewportController();
  controller.setSize(1280, 840);
  if (mode === '3d') controller.setMode('3d');
  return controller;
}

/** Fly a restore to completion, returning every camera it wrote. */
function flyToEnd(controller: ViewportController, fake: FakeClock, stepMs = 40): SavedCamera[] {
  const frames: SavedCamera[] = [];
  for (let guard = 0; guard < 200 && fake.pending() > 0; guard++) {
    fake.advance(stepMs);
    fake.frame();
    frames.push(captureCamera(controller));
  }
  return frames;
}

// ---------------------------------------------------------------------------

describe('an animated restore', () => {
  it('lands EXACTLY on the saved camera, having really travelled', () => {
    const controller = viewport();
    const start = plan(-3200.5, 7150.25, 1.5);
    const target = plan(8123.75, -2044.125, 10.9644);
    controller.setView2d({ centreMm: start.centreMm, mmPerPx: start.mmPerPx });

    const fake = fakeClock();
    const outcome = restoreCamera(controller, target, {
      clock: fake.clock,
      reducedMotion: false,
    });
    expect(outcome.animated).toBe(true);
    expect(outcome.modeRequested).toBe(null);

    const frames = flyToEnd(controller, fake);

    // It really animated: more than one frame, and the frame before the last
    // was NOT already the destination.
    expect(frames.length).toBeGreaterThan(2);
    const penultimate = frames[frames.length - 2];
    expect(penultimate).toBeDefined();
    if (penultimate !== undefined) expect(sameCamera(penultimate, target)).toBe(false);

    // And it landed exactly — the whole point of the feature.
    const landed = captureCamera(controller) as Saved2dCamera;
    expect(landed).toEqual(target);
    expect(landed.centreMm.x).toBe(target.centreMm.x);
    expect(landed.centreMm.y).toBe(target.centreMm.y);
    expect(landed.mmPerPx).toBe(target.mmPerPx);
    expect(sameCamera(landed, target)).toBe(true);
  });

  it('NEGATIVE CONTROL: the last frame is not merely close — the naive value is', () => {
    const controller = viewport();
    controller.setView2d({ centreMm: { x: 0, y: 0 }, mmPerPx: 1.5 });
    const target = plan(0, 0, 10.9644);

    const fake = fakeClock();
    restoreCamera(controller, target, { clock: fake.clock, reducedMotion: false });
    flyToEnd(controller, fake);

    const landed = (captureCamera(controller) as Saved2dCamera).mmPerPx;
    const naive = Math.exp(Math.log(1.5) + (Math.log(10.9644) - Math.log(1.5)) * 1);

    expect(landed).toBe(target.mmPerPx); // what we do
    expect(naive).not.toBe(target.mmPerPx); // what a tween without the guard does
    expect(landed).not.toBe(naive);
  });

  it('flies the 3D camera too, and lands exactly', () => {
    const controller = viewport('3d');
    const fake = fakeClock();
    restoreCamera(controller, ORBIT, { clock: fake.clock, reducedMotion: false });
    const frames = flyToEnd(controller, fake);

    expect(frames.length).toBeGreaterThan(2);
    expect(captureCamera(controller)).toEqual(ORBIT);
  });

  it('is over in one frame once the duration has elapsed', () => {
    const controller = viewport();
    const target = plan(1000, 2000, 5);
    const fake = fakeClock();
    restoreCamera(controller, target, { clock: fake.clock, reducedMotion: false });

    fake.advance(RESTORE_DURATION_MS + 1);
    fake.frame();

    expect(captureCamera(controller)).toEqual(target);
    expect(fake.pending()).toBe(0); // nothing left in the air
  });
});

describe('an instant restore', () => {
  it('honours prefers-reduced-motion and asks for no frames at all', () => {
    const controller = viewport();
    const target = plan(4321, -876, 3.25);
    const fake = fakeClock();

    const outcome = restoreCamera(controller, target, { clock: fake.clock, reducedMotion: true });

    expect(outcome.animated).toBe(false);
    expect(fake.pending()).toBe(0);
    expect(captureCamera(controller)).toEqual(target);
  });

  it('NEGATIVE CONTROL: with reduced motion off, the same call DOES animate', () => {
    const controller = viewport();
    const fake = fakeClock();
    const outcome = restoreCamera(controller, plan(4321, -876, 3.25), {
      clock: fake.clock,
      reducedMotion: false,
    });
    expect(outcome.animated).toBe(true);
    expect(fake.pending()).toBe(1);
  });

  it('lands immediately when the caller says so, or asks for no time', () => {
    for (const options of [{ animate: false }, { durationMs: 0 }]) {
      const controller = viewport();
      const fake = fakeClock();
      const target = plan(11, 22, 8);
      const outcome = restoreCamera(controller, target, {
        clock: fake.clock,
        reducedMotion: false,
        ...options,
      });
      expect(outcome.animated).toBe(false);
      expect(captureCamera(controller)).toEqual(target);
    }
  });

  it('does not animate to where it already is', () => {
    const controller = viewport();
    const target = captureCamera(controller);
    const fake = fakeClock();
    const outcome = restoreCamera(controller, target, {
      clock: fake.clock,
      reducedMotion: false,
    });
    expect(outcome.animated).toBe(false);
    expect(fake.pending()).toBe(0);
  });
});

describe('the user is in charge', () => {
  it('gives up the moment the camera is panned under it', () => {
    const controller = viewport();
    const target = plan(20_000, 20_000, 40);
    const fake = fakeClock();
    restoreCamera(controller, target, { clock: fake.clock, reducedMotion: false });

    fake.advance(40);
    fake.frame();
    const midFlight = captureCamera(controller);

    // The architect grabs the drawing.
    controller.panPx(120, -60);
    const afterPan = captureCamera(controller);
    expect(sameCamera(afterPan, midFlight)).toBe(false);

    fake.advance(40);
    fake.frame();

    // The flight noticed and stopped: the camera is where the USER put it,
    // not one step further along a path they interrupted.
    expect(sameCamera(captureCamera(controller), afterPan)).toBe(true);
    expect(fake.pending()).toBe(0);
  });

  it('gives up when the projection changes mid-flight', () => {
    const controller = viewport();
    const fake = fakeClock();
    restoreCamera(controller, plan(9000, 9000, 30), { clock: fake.clock, reducedMotion: false });

    fake.advance(40);
    fake.frame();

    controller.setMode('3d'); // Tab
    const orbitBefore = captureCamera(controller);

    fake.advance(40);
    fake.frame();

    expect(sameCamera(captureCamera(controller), orbitBefore)).toBe(true);
    expect(fake.pending()).toBe(0);
  });

  it('stops when cancelled, and cancelling twice is harmless', () => {
    const controller = viewport();
    const fake = fakeClock();
    const outcome = restoreCamera(controller, plan(5000, 5000, 25), {
      clock: fake.clock,
      reducedMotion: false,
    });

    fake.advance(40);
    fake.frame();
    const stopped = captureCamera(controller);

    outcome.cancel();
    outcome.cancel();

    fake.advance(400);
    fake.frame();

    expect(sameCamera(captureCamera(controller), stopped)).toBe(true);
    expect(fake.pending()).toBe(0);
  });

  it("cancels the controller's own fit tween before taking the camera", () => {
    // Two writers on one camera is a race whose winner is whichever rAF fires
    // last. The flight must take the field first.
    const controller = viewport();
    const spy = vi.spyOn(controller, 'cancelTween');
    restoreCamera(controller, plan(1, 2, 3), { clock: fakeClock().clock, reducedMotion: true });
    expect(spy).toHaveBeenCalled();
  });
});

describe('restoring across projections', () => {
  it('writes the plan camera while the 3D view is live, and asks for the switch', () => {
    const controller = viewport('3d');
    const orbitBefore = captureCamera(controller);
    const target = plan(3300, -1200, 2.75);
    const asked: string[] = [];

    const outcome = restoreCamera(controller, target, {
      requestMode: (mode) => asked.push(mode),
      reducedMotion: false,
    });

    // 1. the camera the user is looking at is untouched
    expect(sameCamera(captureCamera(controller), orbitBefore)).toBe(true);
    // 2. the plan camera is already framed, exactly, ready for the swap
    expect(controller.view2d.centreMm).toEqual(target.centreMm);
    expect(controller.view2d.mmPerPx).toBe(target.mmPerPx);
    // 3. the app was asked to switch, and told so
    expect(asked).toEqual(['2d']);
    expect(outcome.modeRequested).toBe('2d');
    expect(outcome.animated).toBe(false);
    // 4. and the flight did NOT set the mode itself — `CameraRig` owns that,
    //    via the ui store. Setting it here would leave R3F rendering through
    //    the other camera.
    expect(controller.mode).toBe('3d');
  });

  it('writes the orbit while the plan is live, and asks for 3D', () => {
    const controller = viewport('2d');
    const planBefore = captureCamera(controller);
    const asked: string[] = [];

    const outcome = restoreCamera(controller, ORBIT, {
      requestMode: (mode) => asked.push(mode),
    });

    expect(sameCamera(captureCamera(controller), planBefore)).toBe(true);
    expect(controller.orbit).toEqual({
      targetMm: ORBIT.targetMm,
      distanceMm: ORBIT.distanceMm,
      azimuthDeg: ORBIT.azimuthDeg,
      polarDeg: ORBIT.polarDeg,
    });
    expect(asked).toEqual(['3d']);
    expect(outcome.modeRequested).toBe('3d');
  });

  it('still restores — and still reports — when nothing can switch the mode', () => {
    // A caller with no way to change the projection (a preview, a spec) gets
    // the camera written and is TOLD a switch was needed, rather than being
    // left to guess why nothing appeared to happen.
    const controller = viewport('3d');
    const target = plan(100, 200, 6);
    const outcome = restoreCamera(controller, target);
    expect(controller.view2d.centreMm).toEqual(target.centreMm);
    expect(outcome.modeRequested).toBe('2d');
  });
});

describe('the default wiring', () => {
  it('uses the browser animation frame when no clock is injected', () => {
    // Guards the "module that believed it was registered" shape: every spec
    // above injects a clock, so nothing else here would notice if the default
    // clock stopped scheduling anything at all.
    const controller = viewport();
    const spy = vi.spyOn(globalThis, 'requestAnimationFrame');
    const outcome = restoreCamera(controller, plan(7000, 7000, 20), { reducedMotion: false });
    expect(spy).toHaveBeenCalledTimes(1);
    outcome.cancel();
    spy.mockRestore();
  });

  it('answers the reduced-motion query without throwing, whatever matchMedia does', () => {
    expect(typeof prefersReducedMotion()).toBe('boolean');

    const original = globalThis.matchMedia;
    Object.defineProperty(globalThis, 'matchMedia', {
      configurable: true,
      writable: true,
      value: () => {
        throw new Error('SecurityError');
      },
    });
    expect(prefersReducedMotion()).toBe(false);
    Object.defineProperty(globalThis, 'matchMedia', {
      configurable: true,
      writable: true,
      value: original,
    });
  });
});
