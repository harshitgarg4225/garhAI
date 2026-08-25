/**
 * Spec for the sun frame — the piece between the NOAA answer and the light.
 *
 * The load-bearing claim is the NORTH ROTATION: `plot.northDeg` is "true
 * north, degrees clockwise from model +Y" and solar azimuth is "degrees
 * clockwise from true north", so the model-space bearing is their sum. Get
 * this wrong and every shadow study lies by exactly the plot rotation.
 */

import { describe, expect, it } from 'vitest';

import { compassLabel, computeSunFrame, sunDirectionModel } from './frame';

const BLR = { latDeg: 12.9716, lonDeg: 77.5946 };

describe('sunDirectionModel — the north rotation', () => {
  it('north-up plot (northDeg 0): a due-south sun sits at model −Y', () => {
    const d = sunDirectionModel(180, 0, 0);
    expect(d.x).toBeCloseTo(0, 9);
    expect(d.y).toBeCloseTo(-1, 9);
    expect(d.z).toBeCloseTo(0, 9);
  });

  it('rotating the plot rotates the sun with it: northDeg 90 puts south at −X', () => {
    // northDeg 90 ⇒ true north points at model +X, so true south is model −X.
    const d = sunDirectionModel(180, 0, 90);
    expect(d.x).toBeCloseTo(-1, 9);
    expect(d.y).toBeCloseTo(0, 9);
  });

  it('elevation lifts z and shortens the horizontal component', () => {
    const d = sunDirectionModel(90, 60, 0); // east, 60° up
    expect(d.z).toBeCloseTo(Math.sin(Math.PI / 3), 9);
    expect(d.x).toBeCloseTo(Math.cos(Math.PI / 3), 9);
    expect(Math.hypot(d.x, d.y, d.z)).toBeCloseTo(1, 9);
  });
});

describe('computeSunFrame', () => {
  const noon = computeSunFrame({ year: 2026, month: 3, day: 20 }, 750, BLR.latDeg, BLR.lonDeg, 0);
  const dusk = computeSunFrame({ year: 2026, month: 3, day: 20 }, 1090, BLR.latDeg, BLR.lonDeg, 0);
  const night = computeSunFrame({ year: 2026, month: 3, day: 20 }, 120, BLR.latDeg, BLR.lonDeg, 0);

  it('noon is bright, dusk dimmer, night has zero sun but non-zero fill', () => {
    expect(noon.aboveHorizon).toBe(true);
    expect(night.aboveHorizon).toBe(false);
    expect(noon.sunIntensity).toBeGreaterThan(dusk.sunIntensity);
    expect(night.sunIntensity).toBe(0);
    expect(night.hemiIntensity).toBeGreaterThan(0);
    expect(noon.hemiIntensity).toBeGreaterThan(night.hemiIntensity);
  });

  it('the light warms as the sun drops', () => {
    // Green and blue channels fall toward the horizon; red holds.
    expect(dusk.sunColor[1]).toBeLessThan(noon.sunColor[1]);
    expect(dusk.sunColor[2]).toBeLessThan(noon.sunColor[2]);
    expect(noon.sunColor[0]).toBe(1);
  });

  it('modelAzimuthDeg folds the plot north in and wraps to [0, 360)', () => {
    const rotated = computeSunFrame(
      { year: 2026, month: 3, day: 20 },
      750,
      BLR.latDeg,
      BLR.lonDeg,
      270,
    );
    expect(rotated.modelAzimuthDeg).toBeCloseTo((noon.modelAzimuthDeg + 270) % 360, 6);
    expect(rotated.modelAzimuthDeg).toBeGreaterThanOrEqual(0);
    expect(rotated.modelAzimuthDeg).toBeLessThan(360);
  });

  it('direction agrees with the raw solar answer', () => {
    const d = noon.dirModel;
    expect(Math.hypot(d.x, d.y, d.z)).toBeCloseTo(1, 9);
    expect(d.z).toBeCloseTo(Math.sin((noon.solar.elevationDeg * Math.PI) / 180), 9);
  });
});

describe('compassLabel', () => {
  it('names the 8 sectors and wraps', () => {
    expect(compassLabel(0)).toBe('N');
    expect(compassLabel(44)).toBe('NE');
    expect(compassLabel(90)).toBe('E');
    expect(compassLabel(135)).toBe('SE');
    expect(compassLabel(180)).toBe('S');
    expect(compassLabel(225)).toBe('SW');
    expect(compassLabel(271)).toBe('W');
    expect(compassLabel(337)).toBe('NW');
    expect(compassLabel(359)).toBe('N');
    expect(compassLabel(-90)).toBe('W');
    expect(compassLabel(450)).toBe('E');
  });
});
