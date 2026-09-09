/**
 * The Compliance tab's pure half: storey attribution, override parsing,
 * filter/search/group, and pack-review staleness. Each helper has a negative
 * control so a filter that silently matched everything would fail here.
 */

import { fold, makeTwoRoomPlanWithOpenings, FIXTURE_IDS } from '@garh/model';
import type { Op } from '@garh/model';
import { describe, expect, it } from 'vitest';

import { complianceSchema } from '../../lib/schemas';
import { toComplianceReport } from './report';
import {
  DEFAULT_FILTERS,
  filterIssues,
  groupByStorey,
  packIdsOf,
  packReviewState,
  readRuleOverrides,
  sortBySeverity,
  storeyOfElements,
  storeyOfIssue,
  storeyRefs,
} from './filters';
import type { ComplianceIssueVM } from '../../components';

function issue(
  overrides: Partial<ComplianceIssueVM> & Pick<ComplianceIssueVM, 'ruleId'>,
): ComplianceIssueVM {
  return {
    status: 'fail',
    message: overrides.ruleId,
    elementIds: [],
    fixAvailable: false,
    ...overrides,
  };
}

describe('storey attribution', () => {
  const doc = makeTwoRoomPlanWithOpenings();
  const storeyOf = storeyOfElements(doc);

  it('maps rooms, walls, openings (via their wall), stairs and storeys to a storey', () => {
    const room = doc.house.rooms[0];
    if (room === undefined) throw new Error('fixture has no room');
    expect(storeyOf.get(room.id)).toBe(FIXTURE_IDS.groundStorey);
    expect(storeyOf.get(FIXTURE_IDS.wallSouth)).toBe(FIXTURE_IDS.groundStorey);
    expect(storeyOf.get(FIXTURE_IDS.doorMain)).toBe(FIXTURE_IDS.groundStorey);
    expect(storeyOf.get(FIXTURE_IDS.groundStorey)).toBe(FIXTURE_IDS.groundStorey);
  });

  it('a plot-level rule (no elements, or an edge id) has no storey', () => {
    expect(storeyOfIssue(issue({ ruleId: 'blr.far.max' }), storeyOf)).toBeNull();
    expect(
      storeyOfIssue(issue({ ruleId: 'blr.setback.front', elementIds: ['edge:0'] }), storeyOf),
    ).toBeNull();
  });

  it('NEGATIVE: elements on two storeys make the rule un-attributable, not first-wins', () => {
    const upper: Op = {
      type: 'storey.add',
      payload: { id: FIXTURE_IDS.firstStorey, index: 1, name: 'First Floor', heightMm: 3000 },
    };
    const twoStoreys = fold(doc, upper).model;
    const map = storeyOfElements(twoStoreys);
    const spanning = issue({
      ruleId: 'x',
      elementIds: [FIXTURE_IDS.groundStorey, FIXTURE_IDS.firstStorey],
    });
    expect(storeyOfIssue(spanning, map)).toBeNull();
    expect(storeyRefs(twoStoreys).map((s) => s.name)).toEqual(['Ground Floor', 'First Floor']);
  });
});

describe('readRuleOverrides', () => {
  it('reads the server-stamped acknowledgement and skips the values map and junk', () => {
    const out = readRuleOverrides({
      values: { setbackFrontMm: 1200 },
      'nbc.room.habitable.area.min': {
        reason: 'Client-signed deviation',
        byUserId: 'u1',
        byName: 'Asha Rao',
        at: '2026-09-09T10:00:00Z',
      },
      'blr.far.max': { reason: 'legacy shape' },
      'broken.rule': 'not an object',
      'no.reason': { at: '2026-01-01T00:00:00Z' },
    });
    expect(Object.keys(out).sort()).toEqual(['blr.far.max', 'nbc.room.habitable.area.min']);
    expect(out['nbc.room.habitable.area.min']).toEqual({
      reason: 'Client-signed deviation',
      byUserId: 'u1',
      byName: 'Asha Rao',
      at: '2026-09-09T10:00:00Z',
    });
    expect(out['blr.far.max']).toEqual({
      reason: 'legacy shape',
      byUserId: null,
      byName: null,
      at: null,
    });
  });
});

describe('filterIssues', () => {
  const doc = makeTwoRoomPlanWithOpenings();
  const storeyOf = storeyOfElements(doc);
  const room = doc.house.rooms[0];
  if (room === undefined) throw new Error('fixture has no room');
  const issues: ComplianceIssueVM[] = [
    issue({
      ruleId: 'nbc.room.habitable.area.min',
      packId: 'nbc-core',
      status: 'fail',
      elementIds: [room.id],
      message: 'Bedroom 2 is 8.9 m²',
    }),
    issue({ ruleId: 'blr.far.max', packId: 'blr', status: 'pass', message: 'FAR 1.6 of 1.75' }),
    issue({
      ruleId: 'blr.setback.front',
      packId: 'blr',
      status: 'fail',
      overridden: true,
      message: 'Front setback short',
    }),
    issue({
      ruleId: 'vastu.kitchen.se',
      packId: 'vastu',
      status: 'warn',
      message: 'Kitchen not in SE',
    }),
  ];

  it('the default filter keeps everything (negative control for every other case)', () => {
    expect(filterIssues(issues, DEFAULT_FILTERS, storeyOf)).toHaveLength(4);
  });

  it('by pack', () => {
    expect(
      filterIssues(issues, { ...DEFAULT_FILTERS, pack: 'blr' }, storeyOf).map((i) => i.ruleId),
    ).toEqual(['blr.far.max', 'blr.setback.front']);
  });

  it('by severity, including the "overridden" pseudo-severity', () => {
    expect(filterIssues(issues, { ...DEFAULT_FILTERS, severity: 'fail' }, storeyOf)).toHaveLength(
      2,
    );
    expect(
      filterIssues(issues, { ...DEFAULT_FILTERS, severity: 'overridden' }, storeyOf).map(
        (i) => i.ruleId,
      ),
    ).toEqual(['blr.setback.front']);
    expect(
      filterIssues(issues, { ...DEFAULT_FILTERS, severity: 'not_applicable' }, storeyOf),
    ).toHaveLength(0);
  });

  it('by storey: a room rule is on its storey, plot rules are under "plot"', () => {
    expect(
      filterIssues(issues, { ...DEFAULT_FILTERS, storey: FIXTURE_IDS.groundStorey }, storeyOf).map(
        (i) => i.ruleId,
      ),
    ).toEqual(['nbc.room.habitable.area.min']);
    expect(filterIssues(issues, { ...DEFAULT_FILTERS, storey: 'plot' }, storeyOf)).toHaveLength(3);
  });

  it('search matches rule id, message and citation, case-insensitively', () => {
    expect(filterIssues(issues, { ...DEFAULT_FILTERS, query: 'setback' }, storeyOf)).toHaveLength(
      1,
    );
    expect(filterIssues(issues, { ...DEFAULT_FILTERS, query: 'BEDROOM' }, storeyOf)).toHaveLength(
      1,
    );
    expect(
      filterIssues(issues, { ...DEFAULT_FILTERS, query: 'nothing-here' }, storeyOf),
    ).toHaveLength(0);
  });

  it('groups by storey with plot-level rules last and no empty groups', () => {
    const groups = groupByStorey(issues, storeyRefs(doc), storeyOf);
    expect(groups.map((g) => [g.label, g.issues.length])).toEqual([
      ['Ground Floor', 1],
      ['Plot and whole building', 3],
    ]);
  });

  it('sorts failures first and lists the packs once each', () => {
    expect(sortBySeverity(issues).map((i) => i.status)).toEqual(['fail', 'fail', 'warn', 'pass']);
    expect(packIdsOf(issues)).toEqual(['nbc-core', 'blr', 'vastu']);
  });
});

describe('packReviewState', () => {
  const today = new Date('2026-09-09T00:00:00Z');

  it('a seed pack is unreviewed whatever its dates say', () => {
    expect(
      packReviewState(
        { status: 'unreviewed', lastReviewedAt: null, nextReviewDue: '2020-01-01' },
        today,
      ),
    ).toEqual({
      kind: 'unreviewed',
    });
    expect(packReviewState(undefined, today)).toEqual({ kind: 'unreviewed' });
  });

  it('a reviewed pack is overdue, due within 30 days, or current', () => {
    expect(
      packReviewState(
        { status: 'reviewed', lastReviewedAt: '2026-01-01', nextReviewDue: '2026-09-01' },
        today,
      ),
    ).toEqual({
      kind: 'overdue',
      due: '2026-09-01',
      daysOver: 8,
    });
    expect(
      packReviewState(
        { status: 'reviewed', lastReviewedAt: '2026-01-01', nextReviewDue: '2026-09-30' },
        today,
      ),
    ).toEqual({
      kind: 'due',
      due: '2026-09-30',
      daysLeft: 21,
    });
    expect(
      packReviewState(
        { status: 'reviewed', lastReviewedAt: '2026-01-01', nextReviewDue: '2027-01-01' },
        today,
      ),
    ).toEqual({
      kind: 'current',
      due: '2027-01-01',
    });
    expect(
      packReviewState(
        { status: 'reviewed', lastReviewedAt: '2026-01-01', nextReviewDue: null },
        today,
      ),
    ).toEqual({
      kind: 'current',
      due: null,
    });
  });
});

describe('toComplianceReport', () => {
  it('maps the wire report, counts overridden rows, and keeps areas/warnings/packReview', () => {
    const wire = complianceSchema.parse({
      evaluated: true,
      projectId: 'proj_1',
      live: true,
      results: [
        { ruleId: 'a', status: 'fail', message: 'a', overridden: true, overrideReason: 'ok' },
        { ruleId: 'b', status: 'pass', message: 'b' },
      ],
      counts: { fail: 1, pass: 1 },
      packVersions: { 'nbc-core': '2026.07', junk: 7 },
      packReview: { 'nbc-core': { status: 'unreviewed' } },
      warnings: ['room type x unreachable'],
      areas: {
        plotAreaMm2: 100,
        rows: [{ key: 'plot_area', label: 'Plot area', value: 100, unit: 'mm2' }],
      },
    });
    const vm = toComplianceReport(wire);
    expect(vm.counts).toEqual({ pass: 1, warn: 0, fail: 1, not_applicable: 0, overridden: 1 });
    expect(vm.packVersions).toEqual({ 'nbc-core': '2026.07' });
    expect(vm.packReview['nbc-core']?.status).toBe('unreviewed');
    expect(vm.warnings).toEqual(['room type x unreachable']);
    expect(vm.areas?.rows[0]?.key).toBe('plot_area');
    expect(vm.issues[0]?.overridden).toBe(true);
    expect(vm.issues[0]?.overrideReason).toBe('ok');
  });
});
