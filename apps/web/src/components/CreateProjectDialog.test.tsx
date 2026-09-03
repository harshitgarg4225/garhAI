/**
 * The template picker exists only if the dialog is HANDED the registry.
 *
 * The dashboard fetched `GET /templates` from the first day templates existed and
 * never passed the result to this dialog, so no trial architect ever saw a
 * template (execution find, 2026-09-02). Two gates: the dialog shows the picker
 * when given templates and preselects a deep-linked one; and `DashboardPage`
 * actually passes what it fetched (a source contract — the page needs routing,
 * stores and toasts to render, and a render test that mocked all of them would be
 * asserting the mocks).
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { CreateProjectDialog } from './CreateProjectDialog';

const noop = (): void => undefined;

const PREVIEW =
  'data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%3E%3C%2Fsvg%3E';
const TEMPLATES = [
  {
    id: 'blank',
    name: 'Blank',
    description: '',
    plotSizeLabel: '',
    tags: [],
    kind: 'blank' as const,
  },
  {
    id: 'blr-30x40-g1-3bhk',
    name: 'Bengaluru 30 × 40, G+1 3BHK',
    description: 'A solved plan.',
    plotSizeLabel: '30 × 40 ft',
    tags: ['plan'],
    kind: 'plan' as const,
    previewUrl: PREVIEW,
  },
];

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function radios(): HTMLElement[] {
  return Array.from(document.body.querySelectorAll<HTMLElement>('[role="radio"]'));
}

describe('CreateProjectDialog templates', () => {
  it('shows the picker when it is given the registry', () => {
    act(() =>
      root.render(
        <CreateProjectDialog open onOpenChange={noop} onCreate={noop} templates={TEMPLATES} />,
      ),
    );
    const group = document.body.querySelector(
      '[role="radiogroup"][aria-label="Start from a template"]',
    );
    expect(group).not.toBeNull();
    expect(radios()).toHaveLength(2);
  });

  it('draws the ready-made plan as an image and labels it as one', () => {
    act(() =>
      root.render(
        <CreateProjectDialog open onOpenChange={noop} onCreate={noop} templates={TEMPLATES} />,
      ),
    );
    const images = Array.from(
      document.body.querySelectorAll<HTMLImageElement>('[role="radio"] img'),
    );
    expect(images).toHaveLength(1);
    expect(images[0]?.getAttribute('src')).toBe(PREVIEW);
    expect(document.body.textContent).toContain('Ready-made plan');
  });

  it('shows no picker without a registry — the honest degraded state', () => {
    act(() => root.render(<CreateProjectDialog open onOpenChange={noop} onCreate={noop} />));
    expect(document.body.querySelector('[role="radiogroup"]')).toBeNull();
  });

  it('preselects the template a deep link asked for', () => {
    act(() =>
      root.render(
        <CreateProjectDialog
          open
          onOpenChange={noop}
          onCreate={noop}
          templates={TEMPLATES}
          initialTemplateId="blr-30x40-g1-3bhk"
        />,
      ),
    );
    const checked = radios().filter((r) => r.getAttribute('aria-checked') === 'true');
    expect(checked).toHaveLength(1);
    expect(checked[0]?.textContent).toContain('Bengaluru 30 × 40');
  });

  it('ignores a deep link to a template that does not exist', () => {
    act(() =>
      root.render(
        <CreateProjectDialog
          open
          onOpenChange={noop}
          onCreate={noop}
          templates={TEMPLATES}
          initialTemplateId="no-such-template"
        />,
      ),
    );
    const checked = radios().filter((r) => r.getAttribute('aria-checked') === 'true');
    expect(checked[0]?.textContent).toContain('Blank');
  });
});

describe('DashboardPage wiring', () => {
  it('hands the templates it fetched to the dialog', () => {
    const source = readFileSync(join(__dirname, '..', 'pages', 'DashboardPage.tsx'), 'utf8');
    const dialog = source.slice(source.indexOf('<CreateProjectDialog'));
    const props = dialog.slice(0, dialog.indexOf('/>'));
    expect(props).toContain('templates={templates}');
    expect(props).toContain('initialTemplateId=');
  });
});
