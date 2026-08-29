/**
 * The submission checklist, driven by real clicks on a real DOM.
 *
 * `createRoot` into jsdom with `api.sheets` stubbed at the transport, so what is under
 * test is the panel's own behaviour and not a hand-rolled renderer.
 *
 * Two of these are the ones that matter, and both are shaped after defects this
 * repository has actually shipped:
 *
 *   * **It must not guess the authority.** Bengaluru has two. A panel that quietly
 *     selected the first would look completely correct and would hand half the city a
 *     checklist for the wrong desk. Asserting "asks" rather than "renders something".
 *   * **The tick must never appear alone.** Not one template has been reviewed. A green
 *     "ready" with no seed caveat beside it is this product claiming an assurance it
 *     cannot give, and it would pass any test that only asserted the word "ready".
 *
 * ════════════════════════════════════════════════════════════════════════════
 * NEGATIVE CONTROLS — each break applied, the suite run, the failure observed,
 * the change reverted.
 * ════════════════════════════════════════════════════════════════════════════
 *   A. auto-select the first available authority when none is chosen
 *        → "asks which authority instead of choosing one" fails
 *   B. drop the seed/unreviewed line from the ready branch
 *        → "never shows a tick without saying what it is worth" fails
 *   C. render `shortfalls.length` as a count instead of each `detail`
 *        → "names what is missing in words" fails
 *   D. send the old fields through on an authority switch
 *        → "switching authority does not carry the old identifiers" fails
 */

import { act, type ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '@garh/ui';

import { api } from '../../lib/api';
import { SubmissionPanel } from './SubmissionPanel';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const PROJECT_ID = '11111111-1111-4111-8111-111111111111';

function template(authority: string, shortTitle: string) {
  return {
    authority,
    cityPack: 'blr',
    title: `${shortTitle} plan sanction (seed)`,
    shortTitle,
    citation: 'a bye-law document',
    confidence: 'seed',
    review: 'unreviewed',
    verify: 'Confirm against the current checklist before submitting.',
    paper: 'A2',
    scaleDenominator: 100,
    sheets: [{ kind: 'floor', required: true, note: '' }],
    statutoryFields: [
      {
        key: 'khataNumber',
        label: 'KHATA NO.',
        required: true,
        note: 'On the corporation register.',
      },
    ],
    declarations: [],
  };
}

const BBMP = template('bbmp', 'BBMP');
const BDA = template('bda', 'BDA');

let container: HTMLDivElement;
let root: Root;

function mount(element: ReactElement): void {
  act(() => {
    root.render(element);
  });
}

/** Let the panel's two awaited fetches settle before asserting. */
async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

function text(): string {
  return container.textContent ?? '';
}

function button(label: string): HTMLButtonElement | undefined {
  return Array.from(container.querySelectorAll('button')).find(
    (node) => node.textContent?.trim() === label,
  );
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  try {
    act(() => root.unmount());
  } catch {
    // already unmounted
  }
  container.remove();
  vi.restoreAllMocks();
});

function stub(
  submission: { authority: string | null; fields: Record<string, string>; available: unknown[] },
  readiness: Record<string, unknown>,
) {
  const saveSubmission = vi
    .spyOn(api.sheets, 'saveSubmission')
    .mockImplementation(
      (_id: string, input: { authority: string | null; fields: Record<string, string> }) =>
        Promise.resolve({
          authority: input.authority,
          fields: input.fields,
          available: submission.available,
        } as never),
    );
  vi.spyOn(api.sheets, 'submission').mockResolvedValue(submission as never);
  vi.spyOn(api.sheets, 'submissionReadiness').mockResolvedValue(readiness as never);
  return { saveSubmission };
}

const NOT_READY = {
  projectId: PROJECT_ID,
  authority: 'bbmp',
  title: 'BBMP plan sanction (seed)',
  ready: false,
  shortfalls: [
    { kind: 'field', what: 'khataNumber', detail: 'BBMP wants KHATA NO. in the title block' },
    { kind: 'sheet', what: 'floor', detail: 'BBMP requires a floor sheet' },
  ],
  advisories: [],
  satisfied: 0,
  total: 2,
  confidence: 'seed',
  review: 'unreviewed',
  verify: 'Confirm against the current checklist before submitting.',
  chooseFrom: [],
};

describe('SubmissionPanel', () => {
  it('asks which authority instead of choosing one', async () => {
    stub(
      { authority: null, fields: {}, available: [BBMP, BDA] },
      {
        ...NOT_READY,
        authority: null,
        shortfalls: [],
        chooseFrom: ['bbmp', 'bda'],
      },
    );
    mount(
      <ToastProvider>
        <SubmissionPanel projectId={PROJECT_ID} />
      </ToastProvider>,
    );
    await settle();

    // Both offered, neither selected — the panel states the choice rather than making it.
    expect(button('BBMP')).toBeTruthy();
    expect(button('BDA')).toBeTruthy();
    expect(text()).toContain('we will not guess');
    // ...and no readiness verdict is rendered for a desk nobody picked.
    expect(container.querySelector('[data-testid="submission-readiness"]')).toBeNull();
    // The assertions that actually catch a silent pick. Prompting for a choice while
    // ALSO rendering the first authority's form is exactly the defect: it looks like a
    // question and behaves like an answer.
    expect(text()).not.toContain('KHATA NO.');
    expect(button('Save submission details')).toBeUndefined();
    expect(container.querySelectorAll('input')).toHaveLength(0);
  });

  it('names what is missing in words, not as a count', async () => {
    stub({ authority: 'bbmp', fields: {}, available: [BBMP, BDA] }, NOT_READY);
    mount(
      <ToastProvider>
        <SubmissionPanel projectId={PROJECT_ID} />
      </ToastProvider>,
    );
    await settle();

    expect(text()).toContain('Not ready to submit');
    expect(text()).toContain('BBMP wants KHATA NO. in the title block');
    expect(text()).toContain('BBMP requires a floor sheet');
  });

  it('never shows a tick without saying what it is worth', async () => {
    stub(
      { authority: 'bbmp', fields: { khataNumber: 'A-1' }, available: [BBMP] },
      {
        ...NOT_READY,
        ready: true,
        shortfalls: [],
        satisfied: 2,
      },
    );
    mount(
      <ToastProvider>
        <SubmissionPanel projectId={PROJECT_ID} />
      </ToastProvider>,
    );
    await settle();

    expect(text()).toContain('Has everything the template asks for');
    // The caveat is not optional, and not a tooltip.
    expect(text()).toContain('seed');
    expect(text()).toContain('unreviewed');
    expect(text()).toContain('does not mean the set will be sanctioned');
  });

  it('shows the authority its statutory identifiers, by their real labels', async () => {
    stub({ authority: 'bbmp', fields: {}, available: [BBMP] }, NOT_READY);
    mount(
      <ToastProvider>
        <SubmissionPanel projectId={PROJECT_ID} />
      </ToastProvider>,
    );
    await settle();
    expect(text()).toContain('KHATA NO.');
    expect(text()).toContain('On the corporation register.');
  });

  it('switching authority does not carry the old identifiers across', async () => {
    const { saveSubmission } = stub(
      { authority: 'bbmp', fields: { khataNumber: 'A-1234/56' }, available: [BBMP, BDA] },
      NOT_READY,
    );
    mount(
      <ToastProvider>
        <SubmissionPanel projectId={PROJECT_ID} />
      </ToastProvider>,
    );
    await settle();

    await act(async () => {
      button('BDA')?.click();
      await Promise.resolve();
    });
    await settle();

    // A BBMP khata number must not travel to a set going to BDA.
    expect(saveSubmission).toHaveBeenCalledWith(PROJECT_ID, { authority: 'bda', fields: {} });
  });
});
