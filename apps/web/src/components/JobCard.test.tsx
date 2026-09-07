/**
 * A finished export must hand the architect its file.
 *
 * The row the API returns carries a signed download link; the view-model mapping
 * dropped it, so the Sheets tab's job list showed a green tick and nothing to click,
 * and the browser UAT waited three minutes for a download that could never start.
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { toJobVM } from '../pages/_contracts';
import { JobCard } from './JobCard';
import type { JobVM } from './types';

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const DONE: JobVM = {
  id: 'job-1',
  kind: 'export',
  status: 'succeeded',
  progress: 100,
  queuePosition: null,
  resultHref: 'http://127.0.0.1:8000/api/v1/downloads/tok.en',
  resultLabel: 'Download PDF set',
};

describe('JobCard', () => {
  it('renders a finished export as a real download link', () => {
    act(() => root.render(<JobCard job={DONE} />));
    const link = host.querySelector<HTMLAnchorElement>('a[data-testid="job-download"]');
    expect(link).not.toBeNull();
    expect(link?.getAttribute('href')).toBe(DONE.resultHref);
    expect(link?.hasAttribute('download')).toBe(true);
    expect(link?.textContent).toContain('Download PDF set');
  });

  it('shows no link for a finished job that has no artefact', () => {
    const { resultHref: _href, resultLabel: _label, ...bare } = DONE;
    act(() => root.render(<JobCard job={bare} />));
    expect(host.querySelector('a[data-testid="job-download"]')).toBeNull();
  });
});

describe('toJobVM', () => {
  it("carries the export row's signed link and names the file kind", () => {
    const vm = toJobVM({
      id: 'job-2',
      kind: 'export',
      status: 'succeeded',
      downloadUrl: 'http://api/downloads/x',
      exportKind: 'dxf',
    });
    expect(vm.resultHref).toBe('http://api/downloads/x');
    expect(vm.resultLabel).toBe('Download DXF');
  });

  it('offers no link while the export is still running, even if a stale URL is present', () => {
    const vm = toJobVM({
      id: 'job-3',
      kind: 'export',
      status: 'running',
      downloadUrl: 'http://api/downloads/x',
      exportKind: 'pdf-set',
    });
    expect(vm.resultHref).toBeUndefined();
    expect(vm.resultLabel).toBeUndefined();
  });
});
