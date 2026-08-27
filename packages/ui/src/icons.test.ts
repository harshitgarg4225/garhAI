/**
 * The icon table.
 *
 * `IconName` is a union derived from the path map, so a typo in a name is
 * normally a compile error — but only where the name is a literal. The severity
 * and tool tables map values into `IconName` at runtime, and a glyph deleted
 * from the map would render an empty `<path>` rather than failing loudly. These
 * tests close that gap without needing a DOM: React elements are plain objects,
 * so calling the component and inspecting the result is enough.
 */

import { describe, expect, it } from 'vitest';

import { SEVERITY_ICON } from './Chip';
import { ICON_NAMES, Icon } from './icons';
import type { IconName } from './icons';

/** Pull the `d` attribute out of the element `Icon` returns. */
function pathData(name: IconName): string {
  const element = Icon({ name }) as unknown as {
    props: { children: unknown };
  };
  const children = Array.isArray(element.props.children)
    ? element.props.children
    : [element.props.children];
  for (const child of children) {
    if (
      typeof child === 'object' &&
      child !== null &&
      (child as { type?: unknown }).type === 'path'
    ) {
      const d = (child as { props?: { d?: unknown } }).props?.d;
      return typeof d === 'string' ? d : '';
    }
  }
  return '';
}

describe('ICON_NAMES', () => {
  it('is non-empty and has no duplicates', () => {
    expect(ICON_NAMES.length).toBeGreaterThan(0);
    expect(new Set(ICON_NAMES).size).toBe(ICON_NAMES.length);
  });

  it('uses kebab-case names only', () => {
    for (const name of ICON_NAMES) {
      expect(name).toMatch(/^[a-z0-9]+(-[a-z0-9]+)*$/);
    }
  });
});

describe('every glyph draws something', () => {
  it('has a path that starts with an absolute move', () => {
    for (const name of ICON_NAMES) {
      const d = pathData(name);
      expect(d.length, `icon "${name}" has no path data`).toBeGreaterThan(0);
      expect(d.startsWith('M'), `icon "${name}" does not start with M: ${d.slice(0, 12)}`).toBe(
        true,
      );
    }
  });

  it('stays inside the 24×24 grid', () => {
    // A coordinate outside the viewBox is clipped at render time and shows up
    // as a mysteriously cropped glyph. Cheaper to catch here.
    for (const name of ICON_NAMES) {
      for (const raw of pathData(name).match(/-?\d+(\.\d+)?/g) ?? []) {
        const value = Number(raw);
        expect(value, `icon "${name}" has an out-of-grid coordinate ${raw}`).toBeGreaterThanOrEqual(
          -1,
        );
        expect(value, `icon "${name}" has an out-of-grid coordinate ${raw}`).toBeLessThanOrEqual(
          25,
        );
      }
    }
  });
});

describe('runtime icon lookups resolve', () => {
  it('maps every chip severity to a real glyph or an explicit null', () => {
    for (const [severity, icon] of Object.entries(SEVERITY_ICON)) {
      if (icon === null) continue;
      expect(ICON_NAMES, `severity "${severity}" points at a missing glyph`).toContain(icon);
    }
  });
});

describe('Icon accessibility', () => {
  it('is hidden from screen readers when it has no title', () => {
    const element = Icon({ name: 'check' }) as unknown as { props: Record<string, unknown> };
    expect(element.props['aria-hidden']).toBe(true);
    expect(element.props.role).toBeUndefined();
  });

  it('becomes an img with a title when given one', () => {
    const element = Icon({ name: 'check', title: 'Saved' }) as unknown as {
      props: Record<string, unknown>;
    };
    expect(element.props['aria-hidden']).toBeUndefined();
    expect(element.props.role).toBe('img');
  });
});
