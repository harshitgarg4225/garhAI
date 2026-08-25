/**
 * Score bands and progress semantics.
 *
 * `scoreBand` decides what colour an option card's score ring is, and its edges
 * are not decorative: 55 is the solver's presentability gate (§5.6) and 75 is
 * where a plan stops needing a caveat. If those move, they move here and the
 * solver's gate must move with them — hence the explicit boundary tests.
 *
 * The progress assertions cover the §15 rule that a bar we cannot fill honestly
 * must not claim a percentage: `value: null` has to leave `aria-valuenow`
 * unset, so assistive tech says "busy" rather than "0 percent".
 */

import { describe, expect, it } from 'vitest';

import { ProgressBar, ProgressRing, scoreBand } from './ProgressRing';

describe('scoreBand', () => {
  it('bands at the solver gates, not at round numbers', () => {
    expect(scoreBand(54)).toBe('low');
    expect(scoreBand(55)).toBe('mid');
    expect(scoreBand(74)).toBe('mid');
    expect(scoreBand(75)).toBe('high');
  });

  it('covers the ends of the range', () => {
    expect(scoreBand(0)).toBe('low');
    expect(scoreBand(100)).toBe('high');
  });
});

describe('ProgressRing accessibility', () => {
  it('exposes the value as a progressbar with readable text', () => {
    const el = ProgressRing({ value: 78, label: 'Composite score' }) as unknown as {
      props: Record<string, unknown>;
    };
    expect(el.props['role']).toBe('progressbar');
    expect(el.props['aria-valuenow']).toBe(78);
    expect(el.props['aria-valuetext']).toBe('78 out of 100');
    expect(el.props['aria-label']).toBe('Composite score');
  });

  it('clamps and rounds out-of-range input rather than drawing past the ring', () => {
    const over = ProgressRing({ value: 140, label: 'x' }) as unknown as {
      props: Record<string, unknown>;
    };
    const under = ProgressRing({ value: -20, label: 'x' }) as unknown as {
      props: Record<string, unknown>;
    };
    const fractional = ProgressRing({ value: 62.6, label: 'x' }) as unknown as {
      props: Record<string, unknown>;
    };
    expect(over.props['aria-valuenow']).toBe(100);
    expect(under.props['aria-valuenow']).toBe(0);
    expect(fractional.props['aria-valuenow']).toBe(63);
  });
});

describe('ProgressBar honesty', () => {
  /** The bar element is the second child of the wrapper. */
  function barProps(node: unknown): Record<string, unknown> {
    const children = (node as { props: { children: unknown[] } }).props.children;
    const bar = children.find(
      (child) =>
        typeof child === 'object' &&
        child !== null &&
        (child as { props?: { role?: unknown } }).props?.role === 'progressbar',
    );
    return (bar as { props: Record<string, unknown> }).props;
  }

  it('reports a real percentage when it knows one', () => {
    const props = barProps(ProgressBar({ value: 40, label: 'Progress' }));
    expect(props['aria-valuenow']).toBe(40);
    expect(props['aria-valuemax']).toBe(100);
  });

  it('claims no percentage when the worker cannot report one', () => {
    const props = barProps(ProgressBar({ value: null, label: 'Waiting for a worker' }));
    expect(props['aria-valuenow']).toBeUndefined();
    expect(props['aria-valuemin']).toBeUndefined();
    expect(props['aria-valuemax']).toBeUndefined();
  });
});
