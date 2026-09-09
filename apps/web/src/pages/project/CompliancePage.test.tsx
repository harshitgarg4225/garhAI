/**
 * The Compliance tab, rendered for real with the outlet context the shell gives
 * it, the model store holding a document with one accepted rule, and the API
 * mocked at the override routes.
 *
 * What a professional needs and could not see before is what is asserted:
 * measured vs limit with units, the pack's original limit under a value
 * override, per-element instances, who/when/why on an overridden row with
 * Revoke, "Accept with reason" on failing rows only (with the dialog's
 * short-reason refusal as the control), the failed-re-check banner with its
 * retry, the area statement quoted from the report, the pack review standing,
 * and the search / status filters narrowing the list.
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom';
import { makeTwoRoomPlanWithOpenings } from '@garh/model';
import { ToastProvider } from '@garh/ui';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { formatComplianceValue } from '../../features/compliance/report';
import { toComplianceReport } from '../../features/compliance/report';
import { AppError } from '../../lib/errors';
import { complianceSchema } from '../../lib/schemas';
import { useModelStore } from '../../stores/model';
import { useSessionStore } from '../../stores/session';
import type { ProjectOutletContext } from '../ProjectShell';
import { CompliancePage } from './CompliancePage';

const mocks = vi.hoisted(() => ({
  override: vi.fn(),
  revokeOverride: vi.fn(),
  since: vi.fn(),
  model: vi.fn(),
  append: vi.fn(),
}));
vi.mock('../../lib/api', () => ({
  api: {
    compliance: { override: mocks.override, revokeOverride: mocks.revokeOverride },
    ops: { since: mocks.since, model: mocks.model, append: mocks.append },
  },
}));

const PROJECT_ID = 'proj_01J0000000000000000000P1';
const USER_ID = 'user_01J0000000000000000000U1';

function buildReport(doc: ReturnType<typeof makeTwoRoomPlanWithOpenings>) {
  const room = doc.house.rooms[0];
  const other = doc.house.rooms[1];
  if (room === undefined || other === undefined) throw new Error('fixture needs two rooms');
  return toComplianceReport(
    complianceSchema.parse({
      evaluated: true,
      projectId: PROJECT_ID,
      live: true,
      packVersions: { 'nbc-core': '2026.07', blr: '2026.07' },
      packReview: { 'nbc-core': { status: 'unreviewed' }, blr: { status: 'unreviewed' } },
      counts: { fail: 3, warn: 0, pass: 1, not_applicable: 0, overridden: 1 },
      warnings: ["room type 'study' is selected by 1 rule but no model room can carry it"],
      notes: ['projection: Edge roles are derived: widest road = front.'],
      disclaimers: [{ packId: 'nbc-core', text: 'Advisory only.' }],
      areas: {
        plotAreaMm2: 111_483_648,
        farAchieved: '0.93',
        farAllowed: '1.75',
        overriddenRuleIds: ['blr.setback.front.9m'],
        rows: [
          {
            key: 'plot_area',
            label: 'Plot area',
            value: 111_483_648,
            unit: 'mm2',
            kind: 'informational',
          },
          {
            key: 'far',
            label: 'FAR',
            value: 0.93,
            unit: 'ratio',
            kind: 'allowance',
            allowed: 1.75,
            limitLabel: 'Permissible',
          },
        ],
        setbacks: [
          {
            edgeIndex: 0,
            role: 'front',
            providedMm: 2500,
            requiredMm: 3000,
            shortfallMm: 500,
            status: 'short',
            ruleIds: ['blr.setback.front.9m'],
          },
        ],
      },
      results: [
        {
          ruleId: 'nbc.door.main.width.min',
          packId: 'nbc-core',
          status: 'fail',
          severity: 'fail',
          message: 'Main door is 900 mm — NBC needs 1,000 mm',
          citeShort: 'NBC 2016 Part 3, Cl. 4.4',
          confidence: 'seed',
          checkType: 'opening_width_min',
          unit: 'mm',
          actual: 900,
          limit: 1000,
          fixAvailable: true,
          autofix: { opType: 'opening.resize', strategy: 'resize-opening-to-limit' },
          elements: ['opening_01J0000000000000000000D1'],
          instances: [
            {
              elementId: 'opening_01J0000000000000000000D1',
              label: 'Main door',
              status: 'fail',
              actual: 900,
              limit: 1000,
            },
          ],
        },
        {
          ruleId: 'nbc.room.habitable.area.min',
          packId: 'nbc-core',
          status: 'fail',
          severity: 'fail',
          message: 'Bedroom 2 is 8.9 m² — NBC needs 9.5 m²',
          citeShort: 'NBC 2016 Part 3, Cl. 4.2',
          confidence: 'seed',
          checkType: 'room_area_min',
          unit: 'mm2',
          actual: 8_900_000,
          limit: 9_500_000,
          fixHint: 'Move the shared wall 300 mm into the passage.',
          fixAvailable: true,
          autofix: { opType: 'wall.move', strategy: 'grow-room-to-limit' },
          elements: [other.id],
          instances: [
            {
              elementId: room.id,
              label: 'Bedroom 1',
              status: 'pass',
              actual: 12_000_000,
              limit: 9_500_000,
            },
            {
              elementId: other.id,
              label: 'Bedroom 2',
              status: 'fail',
              actual: 8_900_000,
              limit: 9_500_000,
            },
          ],
        },
        {
          ruleId: 'blr.setback.front.9m',
          packId: 'blr',
          status: 'fail',
          severity: 'fail',
          message: 'Front setback is 2.5 m — BBMP needs 3.0 m',
          citeShort: 'BBMP Bye-laws 2020, Table 6a',
          confidence: 'seed',
          checkType: 'setback_min',
          unit: 'mm',
          actual: 2500,
          limit: 3000,
          overridden: true,
          overrideReason: 'Client-signed deviation letter attached.',
          valueOverridden: true,
          overrideValueKeys: ['setbackFrontMm'],
          originalLimit: 3500,
          elements: ['edge:0'],
        },
        {
          ruleId: 'nbc.stair.riser.max',
          packId: 'nbc-core',
          status: 'pass',
          severity: 'fail',
          message: 'Risers are 167 mm — NBC allows up to 190 mm',
          citeShort: 'NBC 2016 Part 3, Cl. 4.7',
          confidence: 'seed',
          checkType: 'stair_riser_max',
          unit: 'mm',
          actual: 167,
          limit: 190,
        },
      ],
    }),
  );
}

let container: HTMLDivElement;
let root: Root;
let ctx: ProjectOutletContext;
const recheck = vi.fn();
const applyFix = vi.fn();

function render(): void {
  act(() =>
    root.render(
      <ToastProvider>
        <MemoryRouter initialEntries={['/']}>
          <Routes>
            <Route element={<Outlet context={ctx} />}>
              <Route path="/" element={<CompliancePage />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </ToastProvider>,
    ),
  );
}

function rows(): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>('[data-testid="compliance-row"]'));
}

function buttons(text: string, scope: ParentNode = container): HTMLButtonElement[] {
  return Array.from(scope.querySelectorAll<HTMLButtonElement>('button')).filter((b) =>
    (b.textContent ?? '').includes(text),
  );
}

/** Drive a controlled React input/textarea/select through the native setter. */
function setValue(el: HTMLElement, value: string, event: 'input' | 'change'): void {
  const proto =
    el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : el instanceof HTMLSelectElement
        ? HTMLSelectElement.prototype
        : HTMLInputElement.prototype;
  // React tracks the value through the element's own property; setting it via
  // the prototype's accessor is what makes the following event a real change.
  const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
  if (descriptor?.set === undefined) throw new Error('no native value setter');
  // eslint-disable-next-line @typescript-eslint/unbound-method -- Reflect.apply binds `this` to the element.
  Reflect.apply(descriptor.set, el, [value]);
  act(() => {
    el.dispatchEvent(new Event(event, { bubbles: true }));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  const doc = makeTwoRoomPlanWithOpenings();
  const withOverride = {
    ...doc,
    plot: {
      ...doc.plot,
      regProfile: {
        cityPack: 'blr',
        overrides: {
          values: { setbackFrontMm: 3000 },
          'blr.setback.front.9m': {
            reason: 'Client-signed deviation letter attached.',
            byUserId: USER_ID,
            byName: 'Asha Rao',
            at: '2026-09-09T10:30:00Z',
          },
        },
      },
    },
  };
  useModelStore.getState().reset();
  useModelStore.setState({
    doc: withOverride,
    serverDoc: withOverride,
    status: 'ready',
    projectId: PROJECT_ID,
  });
  useSessionStore.setState({
    user: {
      id: USER_ID,
      email: 'asha@studio.test',
      name: 'Asha Rao',
      role: 'admin',
      coaNumber: null,
    },
  });
  const report = buildReport(doc);
  ctx = {
    project: {
      id: PROJECT_ID,
      name: 'Sharma Residence',
    } as unknown as ProjectOutletContext['project'],
    units: 'm',
    jobs: [],
    compliance: report.issues,
    complianceReport: report,
    complianceChecking: false,
    complianceError: null,
    recheckCompliance: recheck,
    applyFix,
    openShare: vi.fn(),
    generate: vi.fn(),
  };
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('CompliancePage — the numbers', () => {
  it('shows measured vs limit with units, the pack limit under a value override, and instances', () => {
    render();
    expect(rows()).toHaveLength(4);
    const door = rows().find((r) => r.dataset.ruleId === 'nbc.door.main.width.min');
    expect(door?.querySelector('[data-testid="actual-vs-limit"]')?.textContent).toBe(
      `${formatComplianceValue(900, 'mm', 'm')} of ${formatComplianceValue(1000, 'mm', 'm')}`,
    );
    // Which bedroom of two failed is listed with its own numbers.
    const bedroom = rows().find((r) => r.dataset.ruleId === 'nbc.room.habitable.area.min');
    expect(bedroom?.textContent).toContain('Bedroom 2');
    expect(bedroom?.textContent).toContain('Bedroom 1');
    // A value override shows the pack's own number next to the architect's.
    const setback = rows().find((r) => r.dataset.ruleId === 'blr.setback.front.9m');
    expect(setback?.querySelector('[data-testid="original-limit"]')?.textContent).toContain(
      formatComplianceValue(3500, 'mm', 'm'),
    );
    expect(setback?.textContent).toContain('setbackFrontMm');
    // Severity, and the pack's review standing on every row.
    expect(door?.textContent).toContain('Fails');
    expect(door?.textContent).toContain('Seed · unreviewed');
  });

  it('renders the report header, the area statement and the overridden list from the report', () => {
    render();
    const header = container.querySelector('[data-testid="report-header"]');
    expect(header?.textContent).toContain('Live');
    expect(header?.querySelector('[data-testid="report-counts"]')?.textContent).toContain(
      '3 failing',
    );
    expect(header?.textContent).toContain('1 overridden');
    expect(header?.textContent).toContain('nbc-core');
    expect(header?.textContent).toContain('seed');
    // Warnings are behind a toggle, present in the DOM for print, shown on click.
    const notes = container.querySelector('[data-testid="report-notes"]');
    expect(notes?.classList.contains('hidden')).toBe(true);
    act(() => buttons('engine warning')[0]?.click());
    expect(notes?.classList.contains('hidden')).toBe(false);
    expect(notes?.textContent).toContain("room type 'study'");

    const areas = container.querySelector('[data-testid="area-statement"]');
    expect(areas).not.toBeNull();
    expect(areas?.querySelector('[data-testid="area-row-far"]')?.textContent).toContain('0.93');
    expect(areas?.querySelector('[data-testid="area-row-far"]')?.textContent).toContain('1.75');
    expect(areas?.textContent).toContain('Short by');
    expect(areas?.querySelector('[data-testid="area-overridden"]')?.textContent).toContain(
      'blr.setback.front.9m',
    );
  });
});

describe('CompliancePage — overrides', () => {
  it('an overridden row shows who, when and why, and Revoke calls the API', async () => {
    mocks.revokeOverride.mockResolvedValue({
      ruleId: 'blr.setback.front.9m',
      revoked: true,
      headIdx: 9,
    });
    mocks.since.mockResolvedValue({
      ops: [],
      sinceIdx: -1,
      headIdx: -1,
      versionBranch: 'v',
      hasMore: false,
    });
    render();
    const setback = rows().find((r) => r.dataset.ruleId === 'blr.setback.front.9m');
    const record = setback?.querySelector('[data-testid="override-record"]');
    expect(record?.textContent).toContain('Accepted by you');
    expect(record?.textContent).toContain('Client-signed deviation letter attached.');
    expect(record?.textContent).toMatch(/09-09-2026/);
    // The row is still a failure — status never changes on override.
    expect(setback?.textContent).toContain('Front setback is 2.5 m');
    // No "Accept with reason" on an already-accepted row.
    expect(buttons('Accept with reason', setback ?? container)).toHaveLength(0);

    await act(async () => {
      buttons('Revoke', setback ?? container)[0]?.click();
      await Promise.resolve();
    });
    expect(mocks.revokeOverride).toHaveBeenCalledWith(PROJECT_ID, 'blr.setback.front.9m');
  });

  it('"Accept with reason" appears on failing rows only, and the dialog refuses a two-character reason', async () => {
    mocks.override.mockResolvedValue({
      ruleId: 'nbc.door.main.width.min',
      reason: 'Heritage door retained',
      byUserId: USER_ID,
      byName: 'Asha Rao',
      at: '2026-09-09T11:00:00Z',
      headIdx: 10,
    });
    mocks.since.mockResolvedValue({
      ops: [],
      sinceIdx: -1,
      headIdx: -1,
      versionBranch: 'v',
      hasMore: false,
    });
    render();
    const pass = rows().find((r) => r.dataset.ruleId === 'nbc.stair.riser.max');
    expect(buttons('Accept with reason', pass ?? container)).toHaveLength(0); // negative control
    const door = rows().find((r) => r.dataset.ruleId === 'nbc.door.main.width.min');
    const accept = buttons('Accept with reason', door ?? container);
    expect(accept).toHaveLength(1);

    act(() => accept[0]?.click());
    const dialog = document.body.querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
    expect(dialog?.textContent).toContain('Main door is 900 mm');
    const textarea = dialog?.querySelector<HTMLTextAreaElement>('textarea');
    if (!textarea) throw new Error('no reason field');

    // Too short: refused client-side, the API is never called.
    setValue(textarea, 'ok', 'input');
    await act(async () => {
      buttons('Accept and log', document.body)[0]?.click();
      await Promise.resolve();
    });
    expect(mocks.override).not.toHaveBeenCalled();
    expect(dialog?.textContent).toContain('at least 3 characters');

    // A real reason: sent with whitespace normalised.
    setValue(textarea, '  Heritage   door retained ', 'input');
    await act(async () => {
      buttons('Accept and log', document.body)[0]?.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(mocks.override).toHaveBeenCalledWith(PROJECT_ID, {
      ruleId: 'nbc.door.main.width.min',
      reason: 'Heritage door retained',
    });
  });
});

describe('CompliancePage — a failed re-check, Fix it, and filters', () => {
  it('shows the failed-check banner with a working retry, and no banner otherwise', () => {
    render();
    expect(container.querySelector('[data-testid="compliance-error-banner"]')).toBeNull();
    ctx = { ...ctx, complianceError: AppError.from(new Error('503 from the rules service')) };
    render();
    const banner = container.querySelector('[data-testid="compliance-error-banner"]');
    expect(banner?.textContent).toContain('last check failed');
    expect(banner?.textContent).toContain('may not describe the current design');
    expect(banner?.textContent).toContain('503');
    act(() => buttons('Re-check now', banner ?? container)[0]?.click());
    expect(recheck).toHaveBeenCalledTimes(1);
    // The stale rows are still on screen — stale beats blank.
    expect(rows()).toHaveLength(4);
  });

  it('"Fix it" is on the client-computable rule only and goes to applyFix', () => {
    render();
    const door = rows().find((r) => r.dataset.ruleId === 'nbc.door.main.width.min');
    const bedroom = rows().find((r) => r.dataset.ruleId === 'nbc.room.habitable.area.min');
    expect(buttons('Fix it', door ?? container)).toHaveLength(1);
    expect(buttons('Fix it', bedroom ?? container)).toHaveLength(0); // wall.move: hint only
    expect(bedroom?.textContent).toContain('Move the shared wall');
    act(() => buttons('Fix it', door ?? container)[0]?.click());
    expect(applyFix).toHaveBeenCalledTimes(1);
    expect(applyFix.mock.calls[0]?.[0]?.ruleId).toBe('nbc.door.main.width.min');
  });

  it('search and the status filter narrow the list; clearing restores it', () => {
    render();
    const search = container.querySelector<HTMLInputElement>('input[type="search"]');
    if (!search) throw new Error('no search box');
    setValue(search, 'setback', 'input');
    expect(rows()).toHaveLength(1);
    expect(container.textContent).toContain('1 of 4 results shown');
    setValue(search, '', 'input');
    expect(rows()).toHaveLength(4);

    const status = container.querySelector<HTMLSelectElement>('select[aria-label="Status"]');
    if (!status) throw new Error('no status select');
    setValue(status, 'pass', 'change');
    expect(rows().map((r) => r.dataset.ruleId)).toEqual(['nbc.stair.riser.max']);
    setValue(status, 'overridden', 'change');
    expect(rows().map((r) => r.dataset.ruleId)).toEqual(['blr.setback.front.9m']);
    act(() => buttons('Clear filters')[0]?.click());
    expect(rows()).toHaveLength(4);
  });

  it('group by storey puts the room rule under its storey and the rest under the plot', () => {
    render();
    const toggle = Array.from(
      container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]'),
    )[0];
    if (!toggle) throw new Error('no group toggle');
    act(() => toggle.click());
    const headings = Array.from(container.querySelectorAll('h2')).map((h) => h.textContent);
    expect(headings).toContain('Ground Floor');
    expect(headings).toContain('Plot and whole building');
  });
});
