/**
 * The wheel rule, one synthetic event per device — and the controls that
 * prove it can go red (a mouse notch must NOT pan; a two-finger scroll must
 * NOT zoom).
 */

import { describe, expect, it } from 'vitest';

import {
  MAX_PINCH_FACTOR,
  classifyWheel,
  pinchDelta,
  pinchState,
  pinchZoomFactor,
  type WheelLike,
} from './wheelGesture';

function wheel(patch: Partial<WheelLike>): WheelLike {
  return {
    deltaX: 0,
    deltaY: 0,
    deltaMode: 0,
    ctrlKey: false,
    metaKey: false,
    shiftKey: false,
    ...patch,
  };
}

describe('classifyWheel', () => {
  it('a mouse notch zooms (Chrome: deltaY 100, wheelDeltaY −120)', () => {
    const g = classifyWheel(wheel({ deltaY: 100, wheelDeltaY: -120 }));
    expect(g.kind).toBe('zoom');
    expect(g.kind === 'zoom' && g.pinch).toBe(false);
  });

  it('a mouse notch zooms without the legacy field (deltaY 100, no wheelDeltaY)', () => {
    expect(classifyWheel(wheel({ deltaY: -100 })).kind).toBe('zoom');
    expect(classifyWheel(wheel({ deltaY: 120 })).kind).toBe('zoom');
  });

  it('a line-mode wheel zooms (Firefox mouse)', () => {
    expect(classifyWheel(wheel({ deltaY: 3, deltaMode: 1 })).kind).toBe('zoom');
  });

  it('a two-finger scroll PANS the content against the scroll (trackpad, Chrome)', () => {
    // Chrome trackpad: small deltas, legacy field not a notch multiple.
    const g = classifyWheel(wheel({ deltaX: 0, deltaY: 8, wheelDeltaY: -24 }));
    expect(g).toEqual({ kind: 'pan', dxPx: 0, dyPx: -8 });
  });

  it('a two-finger scroll pans on the fractional delta alone (Firefox/Safari trackpad)', () => {
    const g = classifyWheel(wheel({ deltaX: 0, deltaY: 6.5 }));
    expect(g).toEqual({ kind: 'pan', dxPx: 0, dyPx: -6.5 });
  });

  it('any horizontal component pans, in both axes at once', () => {
    const g = classifyWheel(wheel({ deltaX: -12, deltaY: 30, wheelDeltaY: -120 }));
    // Even a notch-looking wheelDeltaY: a wheel has no X.
    expect(g).toEqual({ kind: 'pan', dxPx: 12, dyPx: -30 });
  });

  it('a pinch (ctrl+wheel) zooms and is flagged as a pinch', () => {
    const g = classifyWheel(wheel({ deltaY: -6, ctrlKey: true }));
    expect(g).toEqual({ kind: 'zoom', pinch: true, deltaY: -6, deltaMode: 0 });
  });

  it('⌘+wheel zooms on any device, but is not a pinch', () => {
    const g = classifyWheel(wheel({ deltaY: 8, metaKey: true, wheelDeltaY: -24 }));
    expect(g).toEqual({ kind: 'zoom', pinch: false, deltaY: 8, deltaMode: 0 });
  });

  it('shift+wheel pans sideways', () => {
    expect(classifyWheel(wheel({ deltaY: 100, shiftKey: true, wheelDeltaY: -120 }))).toEqual({
      kind: 'pan',
      dxPx: -100,
      dyPx: 0,
    });
    expect(classifyWheel(wheel({ deltaY: 2, deltaMode: 1, shiftKey: true }))).toEqual({
      kind: 'pan',
      dxPx: -32,
      dyPx: 0,
    });
  });

  it('negative controls: the two devices are never confused for each other', () => {
    expect(classifyWheel(wheel({ deltaY: 100, wheelDeltaY: -120 })).kind).not.toBe('pan');
    expect(classifyWheel(wheel({ deltaY: 4.2 })).kind).not.toBe('zoom');
  });
});

describe('pinchZoomFactor', () => {
  it('is exponential, so spread then pinch back returns to 1', () => {
    expect(pinchZoomFactor(12) * pinchZoomFactor(-12)).toBeCloseTo(1, 12);
    expect(pinchZoomFactor(0)).toBe(1);
  });

  it('is clamped per event', () => {
    expect(pinchZoomFactor(1000)).toBe(MAX_PINCH_FACTOR);
    expect(pinchZoomFactor(-1000)).toBeCloseTo(1 / MAX_PINCH_FACTOR, 12);
  });
});

describe('two-finger touch', () => {
  it('pans by the centroid and zooms in when the fingers spread', () => {
    const before = pinchState({ x: 100, y: 100 }, { x: 200, y: 100 });
    const after = pinchState({ x: 90, y: 110 }, { x: 230, y: 110 });
    const d = pinchDelta(before, after);
    expect(d.dxPx).toBe(10);
    expect(d.dyPx).toBe(10);
    // 100 px apart → 140 px apart: fewer mm per px.
    expect(d.factor).toBeCloseTo(100 / 140, 12);
  });

  it('leaves the zoom alone when a spread is degenerate', () => {
    const before = pinchState({ x: 100, y: 100 }, { x: 100, y: 100 });
    const after = pinchState({ x: 100, y: 100 }, { x: 160, y: 100 });
    expect(pinchDelta(before, after).factor).toBe(1);
  });
});
