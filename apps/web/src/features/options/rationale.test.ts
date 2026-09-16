/**
 * Every fact kind the solver emits has a sentence here, and every sentence
 * carries the fact's number. The renderer is a template, not a model: a test
 * that fails means a sentence changed, never that a sentence was invented.
 *
 * The Vastu verdicts come from the option's compliance rows — a `pass` on
 * `vastu.kitchen.zone` is what makes "as Vastu asks" true — so the tests seed
 * rows rather than letting the renderer guess from the zone letter.
 */

import { describe, expect, it } from 'vitest';

import { formatSqft } from '@garh/model';

import {
  PROSE_FACT_KINDS,
  parseFacts,
  percentOf,
  rationaleLines,
  ratioText,
  unrenderedFacts,
  zoneName,
} from './rationale';
import { planOptionSchema, type PlanOption } from './types';

function option(
  facts: readonly string[],
  extra: Partial<Record<string, unknown>> = {},
): PlanOption {
  return planOptionSchema.parse({
    id: 'plan_x',
    rank: 0,
    scores: {
      targetAreaFit: 88,
      adjacency: 100,
      circulation: 0,
      daylight: 69,
      vastu: 39,
      furnitureFit: 100,
      plumbingStack: 100,
      privacy: 100,
      compactness: 100,
      composite: 73,
      circulationPercent: 18,
    },
    ops: [],
    signature: [],
    stairAnchorId: 'st-s0-start',
    builtUpMm2: 120_000_000,
    footprintMm2: 60_000_000,
    rationaleFacts: facts,
    assumptions: [],
    compliance: [],
    ...extra,
  });
}

function textOf(facts: readonly string[], extra: Partial<Record<string, unknown>> = {}): string[] {
  return rationaleLines(option(facts, extra)).map((line) => line.text);
}

function vastuRow(ruleId: string, status: 'pass' | 'warn' | 'fail', allow: string[]) {
  return { ruleId, packId: 'vastu', status, actual: ['SE'], limit: { allow }, hard: false };
}

describe('parseFacts', () => {
  it('groups values by kind, keeps order, ignores malformed tokens', () => {
    const facts = parseFacts(['zone:kitchen@SE', 'zone:bedroom@N', 'composite:73', 'junk', ':x']);
    expect(facts.get('zone')).toEqual(['kitchen@SE', 'bedroom@N']);
    expect(facts.get('composite')).toEqual(['73']);
    expect(facts.has('junk')).toBe(false);
  });
});

describe('arithmetic helpers', () => {
  it('rounds percentages half-up in integers', () => {
    expect(percentOf(41_000_000, 111_483_648)).toBe(37);
    expect(percentOf(1, 200)).toBe(1); // 0.5% → 1
    expect(percentOf(0, 0)).toBe(0);
  });
  it('formats ratios to two decimals', () => {
    expect(ratioText(175, 100)).toBe('1.75');
    expect(ratioText(41_000_000, 111_483_648)).toBe('0.37');
    expect(ratioText(5, 1000)).toBe('0.01'); // 0.005 rounds up
  });
  it('names zones', () => {
    expect(zoneName('SE')).toBe('south-east');
    expect(zoneName('C')).toBe('centre');
    expect(zoneName('XX')).toBe('xx');
  });
});

describe('rationaleLines — one sentence per fact kind', () => {
  it('composite: the overall score with its strongest and weakest components', () => {
    const [overall] = textOf(['composite:73']);
    expect(overall).toBe(
      'Overall 73 of 100 — strongest on adjacencies (100) and furniture fit (100), weakest on circulation (0).',
    );
  });

  it('bedrooms/baths/storeys/builtUp/footprint: the programme as placed, in sq ft', () => {
    const lines = textOf([
      'bedrooms:3',
      'baths:2',
      'storeys:2',
      'builtUp:120000000',
      'footprint:60000000',
    ]);
    expect(lines).toContain(
      `3 bedrooms and 2 baths over 2 floors — ${formatSqft(120_000_000, 0)} built up on a ${formatSqft(60_000_000, 0)} ground-floor footprint.`,
    );
    expect(textOf(['bedrooms:1', 'baths:1', 'storeys:1'])).toContain(
      '1 bedroom and 1 bath over 1 floor.',
    );
  });

  it('coverage + plotArea: percent of the plot against the pack allowance', () => {
    const lines = textOf(['coverage:41000000/66890188', 'plotArea:111483648']);
    expect(lines).toContain('Ground coverage 37% of the plot, against the 60% the pack allows.');
    // Without the plot area there is no honest percentage, so no sentence.
    expect(textOf(['coverage:41000000/66890188']).join(' ')).not.toContain('coverage');
  });

  it('far + plotArea: the ratio against the allowance', () => {
    const lines = textOf(['far:120000000/195096384', 'plotArea:111483648']);
    expect(lines).toContain('FAR 1.08 against 1.75 allowed.');
  });

  it('mainDoor: the side, with the Vastu entrance verdict from the rows', () => {
    expect(textOf(['mainDoor:S'])).toContain('Main door on the south side.');
    expect(
      textOf(['mainDoor:N'], {
        compliance: [vastuRow('vastu.entrance.edge', 'pass', ['N', 'NE', 'E'])],
      }),
    ).toContain('Main door on the north side, where Vastu prefers it.');
    expect(
      textOf(['mainDoor:S'], {
        compliance: [vastuRow('vastu.entrance.edge', 'warn', ['N', 'NE', 'E'])],
      }),
    ).toContain(
      'Main door on the south side — Vastu prefers an entrance on the north, north-east and east.',
    );
  });

  it('zone: each room with its Vastu verdict, Vastu rooms first, the stair excluded', () => {
    const lines = textOf(
      [
        'zone:bedroom@N',
        'zone:staircase@SW',
        'zone:kitchen@SE',
        'zone:bedroom_master@NE',
        'zone:pooja@NW',
      ],
      {
        compliance: [
          vastuRow('vastu.kitchen.zone', 'pass', ['SE']),
          vastuRow('vastu.master.zone', 'fail', ['SW']),
          vastuRow('vastu.pooja.zone', 'warn', ['NE']),
        ],
      },
    );
    const zones = lines.filter((l) => l.includes(' in the ') && !l.startsWith('Stair'));
    expect(zones).toEqual([
      'Kitchen in the south-east, as Vastu asks.',
      'Master Bedroom in the north-east; Vastu places it in the south-west.',
      "Pooja in the north-west — Vastu's accepted fallback.",
      'Bedroom in the north.',
    ]);
  });

  it('stair: zone, Vastu verdict and the riser schedule', () => {
    expect(
      textOf(['zone:staircase@SE', 'stair:dogleg@storey0:18x167mm'], {
        compliance: [vastuRow('vastu.stair.zone', 'pass', ['S', 'SW', 'W'])],
      }),
    ).toContain(
      'Stair in the south-east, where Vastu allows it — a dogleg flight of 18 risers at 167 mm.',
    );
    expect(textOf(['stair:straight@storey0:17x176mm'])).toContain(
      'Stair — a straight flight of 17 risers at 176 mm.',
    );
  });

  it('circulationPercent: the measured share against the §5.6 cap', () => {
    expect(textOf(['circulationPercent:14'])).toContain(
      'Circulation takes 14% of the floor area; the cap is 18%.',
    );
  });

  it('doors/windows/ventilators: counts, ventilators only when there are any', () => {
    expect(textOf(['doors:9', 'windows:7', 'ventilators:2'])).toContain(
      '9 doors, 7 windows and 2 ventilators placed.',
    );
    expect(textOf(['doors:1', 'windows:7', 'ventilators:0'])).toContain(
      '1 door and 7 windows placed.',
    );
  });

  it('vastu + vastuMode: the score and the mode, or that Vastu is off', () => {
    expect(textOf(['vastu:39', 'vastuMode:advisory'])).toContain(
      'Vastu 39 of 100 in advisory mode.',
    );
    expect(textOf(['vastu:0', 'vastuMode:off'])).toContain('Vastu is off for this brief.');
  });

  it('rules + warnings: the tally, with fails derived and warnings named', () => {
    expect(textOf(['rules:41/41', 'warnings:0'])).toContain(
      'Passes 41 of 41 applicable rule checks.',
    );
    expect(textOf(['rules:38/41', 'warnings:3'])).toContain(
      'Passes 38 of 41 applicable rule checks. 3 advisory warnings to review.',
    );
    expect(textOf(['rules:39/41', 'warnings:1'])).toContain(
      'Passes 39 of 41 applicable rule checks (1 fails). 1 advisory warning to review.',
    );
  });

  it('parking: bays, their size and where they sit', () => {
    expect(textOf(['parking:1@front:2500x5000'])).toContain(
      '1 car bay of 2.5 × 5 m in the front setback, reachable from the road.',
    );
    expect(textOf(['parking:2@front'])).toContain(
      '2 car bays in the front setback, reachable from the road.',
    );
    expect(textOf(['parking:0@front:2500x5000'])).toContain(
      'No car bay could be placed in the front setback.',
    );
  });

  it('daylight and stairAnchor are consumed (scores / details), never rendered raw', () => {
    const lines = textOf(['daylight:69', 'stairAnchor:st-s0-start']);
    expect(lines.join(' ')).not.toContain('daylight:');
    expect(lines.join(' ')).not.toContain('st-s0-start');
    expect(unrenderedFacts(option(['daylight:69', 'stairAnchor:st-s0-start']))).toEqual([]);
  });

  it('an unknown fact kind is never turned into prose and stays a chip', () => {
    const facts = ['composite:73', 'quantumFlux:9'];
    const lines = textOf(facts);
    expect(lines.join(' ')).not.toContain('quantumFlux');
    expect(unrenderedFacts(option(facts))).toEqual(['quantumFlux:9']);
    expect(PROSE_FACT_KINDS).not.toContain('quantumFlux');
  });

  it('reads in a fixed order: overall, programme, envelope, entrance, rooms, stair, circulation, openings, Vastu, rules', () => {
    const kinds = rationaleLines(
      option([
        'composite:73',
        'circulationPercent:18',
        'vastu:39',
        'daylight:69',
        'stairAnchor:st-s0-start',
        'zone:kitchen@SE',
        'zone:staircase@SW',
        'storeys:2',
        'bedrooms:3',
        'baths:2',
        'footprint:60000000',
        'builtUp:120000000',
        'plotArea:111483648',
        'vastuMode:advisory',
        'mainDoor:S',
        'stair:dogleg@storey0:18x167mm',
        'doors:9',
        'windows:7',
        'rules:41/41',
        'warnings:0',
        'coverage:60000000/66890188',
        'far:120000000/195096384',
      ]),
    ).map((line) => line.kind);
    expect(kinds).toEqual([
      'composite',
      'programme',
      'coverage',
      'far',
      'mainDoor',
      'zone',
      'stair',
      'circulationPercent',
      'openings',
      'vastu',
      'rules',
    ]);
  });
});
