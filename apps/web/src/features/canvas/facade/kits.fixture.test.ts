/**
 * kits.fixture.test.ts — `kits.ts` is a MIRROR of
 * `fixtures/catalog/facade-kits.json` (the catalogue of record, served by
 * `GET /catalog/facade-kits` and validated by the seed). This spec is the pin
 * that keeps mirror and record byte-equal: change one without the other and
 * this fails.
 *
 * Read with `readFileSync` rather than a JSON import so the test does not
 * depend on the bundler resolving a path outside `apps/web/src`.
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { colorwayById, FACADE_KITS, kitById } from './kits';

const FIXTURE_PATH = fileURLToPath(
  new URL('../../../../../../fixtures/catalog/facade-kits.json', import.meta.url),
);

describe('kit definitions mirror the fixture catalogue', () => {
  it('deep-equals fixtures/catalog/facade-kits.json', () => {
    const fixture: unknown = JSON.parse(readFileSync(FIXTURE_PATH, 'utf8'));
    expect(fixture).toEqual(FACADE_KITS);
  });

  it('every length in every kit is a safe integer (the mm rule)', () => {
    const walk = (v: unknown, path: string): void => {
      if (typeof v === 'number') {
        expect(Number.isSafeInteger(v), `${path} must be integer`).toBe(true);
        return;
      }
      if (Array.isArray(v)) {
        v.forEach((item, i) => {
          walk(item, `${path}[${String(i)}]`);
        });
        return;
      }
      if (typeof v === 'object' && v !== null) {
        for (const [k, inner] of Object.entries(v)) walk(inner, `${path}.${k}`);
      }
    };
    walk(FACADE_KITS, 'kits');
  });
});

describe('kit lookups', () => {
  it('kitById finds both launch kits and returns null otherwise', () => {
    expect(kitById('contemporary')?.name).toBe('Contemporary');
    expect(kitById('modern-minimal')?.name).toBe('Modern Minimal');
    expect(kitById('art-deco')).toBeNull();
    expect(kitById(null)).toBeNull();
  });

  it('colorwayById falls back to the first colorway, never throws on bad ids', () => {
    const kit = kitById('contemporary');
    if (kit === null) throw new Error('fixture kit missing');
    expect(colorwayById(kit, 'warm-grey').id).toBe('warm-grey');
    expect(colorwayById(kit, 'does-not-exist').id).toBe('mono-wood');
    expect(colorwayById(kit, null).id).toBe('mono-wood');
  });
});
