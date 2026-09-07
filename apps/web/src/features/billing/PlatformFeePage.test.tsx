/**
 * The owner's fee page, rendered for real over a stub transport.
 *
 * What is pinned: the fee in force with who/when, the form shown only when the server
 * says `canSet`, every validation branch beside the field, the confirm that names the
 * next-charge rule, the PUT body, the page reflecting the server's answer (not the
 * typed text), and the 403 branch for a caller the allowlist refuses.
 */

import { act, type ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { ToastProvider } from '@garh/ui';

import { createApiClient } from '../../lib/api';
import { HttpClient } from '../../lib/http';
import { TokenStore } from '../../lib/tokens';
import { PlatformFeePage } from './PlatformFeePage';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const BASE = 'http://api.test/api/v1';

interface Call {
  url: string;
  method: string;
  body: unknown;
}

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => null },
    text: () => Promise.resolve(JSON.stringify(body)),
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

const MARKUP_DEFAULT = {
  percent: '5',
  bps: 500,
  source: 'default',
  updatedAt: null,
  updatedBy: null,
  updatedByEmail: null,
  canSet: true,
};

const MARKUP_SET = {
  percent: '7.5',
  bps: 750,
  source: 'setting',
  updatedAt: '2026-09-07T08:35:00+00:00',
  updatedBy: '11111111-1111-4111-8111-111111111111',
  updatedByEmail: 'owner@garh.example',
  canSet: true,
};

const USAGE = {
  planCode: 'free',
  effectivePlanCode: 'free',
  periodStart: '2026-09-01T00:00:00+00:00',
  periodEnd: '2026-10-01T00:00:00+00:00',
  lines: [{ kind: 'solver', used: 2, allowance: 10, remaining: 8 }],
  spend: {
    capUsd: '$5.00',
    spentUsd: '$1.05',
    remainingUsd: '$3.95',
    capMicros: 5_000_000,
    spentMicros: 1_050_000,
    remainingMicros: 3_950_000,
    enforced: true,
    markupPercent: '5',
    providerCostUsd: '$1.00',
    providerCostMicros: 1_000_000,
  },
};

let calls: Call[];
let respond: (call: Call) => Response;
let container: HTMLDivElement;
let root: Root;

function client() {
  const tokens = new TokenStore();
  tokens.set({ accessToken: 'a1', expiresInSeconds: 900, refreshToken: 'r1' });
  const http = new HttpClient({
    baseUrl: BASE,
    tokens,
    fetchImpl: (input, init) => {
      const call: Call = {
        url: String(input),
        method: init?.method ?? 'GET',
        body: typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : null,
      };
      calls.push(call);
      return Promise.resolve(respond(call));
    },
  });
  return createApiClient(http);
}

async function flush(): Promise<void> {
  for (let i = 0; i < 6; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function mount(element: ReactElement): Promise<void> {
  act(() => {
    root.render(
      <ToastProvider>
        <MemoryRouter>{element}</MemoryRouter>
      </ToastProvider>,
    );
  });
  await flush();
}

function respondWith(markup: unknown): void {
  respond = (call) => {
    if (call.url.endsWith('/admin/billing/markup') && call.method === 'GET') {
      return jsonResponse(200, markup);
    }
    if (call.url.endsWith('/billing/usage')) return jsonResponse(200, USAGE);
    return jsonResponse(404, { code: 'not_found', message: 'no', action: 'no' });
  };
}

function input(): HTMLInputElement {
  const el = container.querySelector('form input');
  if (!(el instanceof HTMLInputElement)) throw new Error('no fee input rendered');
  return el;
}

function setValue(el: HTMLInputElement, value: string): void {
  // React tracks the input's value through its own setter, so a plain `el.value = x`
  // is invisible to `onChange`; the prototype setter is the documented way around it.
  // eslint-disable-next-line @typescript-eslint/unbound-method -- called with an explicit receiver below
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  setter?.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

async function type(value: string): Promise<void> {
  act(() => {
    setValue(input(), value);
  });
  await flush();
}

async function submit(): Promise<void> {
  act(() => {
    const form = container.querySelector('form');
    form?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
  await flush();
}

function dialog(): HTMLElement | null {
  return document.querySelector('[role="dialog"], [role="alertdialog"]');
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  sessionStorage.clear();
  calls = [];
  respondWith(MARKUP_DEFAULT);
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('PlatformFeePage', () => {
  it('shows the fee in force, the boot-default provenance, and what usage says', async () => {
    await mount(<PlatformFeePage client={client()} />);
    const text = container.textContent ?? '';
    expect(container.querySelector('[data-testid="fee-in-force"]')?.textContent).toBe('5%');
    expect(text).toContain('no owner has changed it yet');
    expect(container.querySelector('[data-testid="usage-fee"]')?.textContent).toBe(
      '5% platform fee',
    );
    expect(text).toContain('Provider cost $1.00 + platform fee $0.05 = $1.05 charged');
    expect(container.querySelector('form')).not.toBeNull();
  });

  it('names who set it and when, for a fee an owner set', async () => {
    respondWith(MARKUP_SET);
    await mount(<PlatformFeePage client={client()} />);
    expect(container.querySelector('[data-testid="fee-in-force"]')?.textContent).toBe('7.5%');
    expect(container.textContent).toContain('Set by owner@garh.example on 07-09-2026');
  });

  it('renders read-only for a caller the server says may not set it', async () => {
    respondWith({ ...MARKUP_SET, canSet: false });
    await mount(<PlatformFeePage client={client()} />);
    expect(container.querySelector('form')).toBeNull();
    expect(container.querySelector('[data-testid="fee-read-only"]')?.textContent).toContain(
      'Only a platform owner can change the fee',
    );
    expect(container.querySelector('[data-testid="fee-in-force"]')?.textContent).toBe('7.5%');
  });

  it('refuses bad input beside the field and never opens the confirm', async () => {
    await mount(<PlatformFeePage client={client()} />);

    await submit();
    expect(container.textContent).toContain('Enter a percentage');

    await type('5.005');
    await submit();
    expect(container.textContent).toContain('At most two decimals');

    await type('101');
    await submit();
    expect(container.textContent).toContain('between 0 and 100');

    await type('abc');
    await submit();
    expect(container.textContent).toContain('plain number');

    expect(dialog()).toBeNull();
    expect(calls.filter((c) => c.method === 'PUT')).toHaveLength(0);
  });

  it('treats the fee already in force as nothing to change', async () => {
    await mount(<PlatformFeePage client={client()} />);
    await type('5.00');
    await flush();
    expect(container.querySelector('[data-testid="fee-unchanged"]')).not.toBeNull();
    const button = container.querySelector('form button[type="submit"]');
    expect(button).toBeInstanceOf(HTMLButtonElement);
    expect((button as HTMLButtonElement).disabled).toBe(true);
    await submit();
    expect(dialog()).toBeNull();
  });

  it('confirms with the next-charge rule, PUTs the normalised percent, and shows the server answer', async () => {
    const answer = { ...MARKUP_SET, percent: '7.5', bps: 750 };
    respond = (call) => {
      if (call.url.endsWith('/admin/billing/markup') && call.method === 'PUT') {
        return jsonResponse(200, answer);
      }
      if (call.url.endsWith('/admin/billing/markup')) return jsonResponse(200, MARKUP_DEFAULT);
      if (call.url.endsWith('/billing/usage')) return jsonResponse(200, USAGE);
      return jsonResponse(404, {});
    };
    await mount(<PlatformFeePage client={client()} />);
    const usageReadsBefore = calls.filter((c) => c.url.endsWith('/billing/usage')).length;

    await type(' 7.50 % ');
    await submit();

    const box = dialog();
    expect(box).not.toBeNull();
    expect(box?.textContent).toContain('Change the platform fee to 7.5%?');
    expect(box?.textContent).toContain('From the next metered charge');
    expect(box?.textContent).toContain('keep the fee they were charged at');

    const confirm = Array.from(box?.querySelectorAll('button') ?? []).find((b) =>
      (b.textContent ?? '').includes('Apply from the next charge'),
    );
    expect(confirm).toBeDefined();
    act(() => {
      confirm?.click();
    });
    await flush();

    const put = calls.find((c) => c.method === 'PUT');
    expect(put?.url).toBe(`${BASE}/admin/billing/markup`);
    expect(put?.body).toEqual({ percent: '7.5' });

    expect(dialog()).toBeNull();
    expect(container.querySelector('[data-testid="fee-in-force"]')?.textContent).toBe('7.5%');
    expect(container.textContent).toContain('Set by owner@garh.example');
    expect(input().value).toBe('');
    // Usage is re-read after a change so the "what architects see" card is current.
    expect(calls.filter((c) => c.url.endsWith('/billing/usage')).length).toBeGreaterThan(
      usageReadsBefore,
    );
    expect(document.body.textContent).toContain('Platform fee is now 7.5%');
  });

  it("shows the server's refusal when the allowlist says no", async () => {
    respond = (call) => {
      if (call.url.endsWith('/admin/billing/markup') && call.method === 'PUT') {
        return jsonResponse(403, {
          code: 'forbidden',
          message: 'Only a platform owner can change this.',
          action: 'Sign in as a platform owner to change this.',
        });
      }
      if (call.url.endsWith('/admin/billing/markup')) return jsonResponse(200, MARKUP_DEFAULT);
      if (call.url.endsWith('/billing/usage')) return jsonResponse(200, USAGE);
      return jsonResponse(404, {});
    };
    await mount(<PlatformFeePage client={client()} />);
    await type('9');
    await submit();
    const confirm = Array.from(dialog()?.querySelectorAll('button') ?? []).find((b) =>
      (b.textContent ?? '').includes('Apply from the next charge'),
    );
    act(() => {
      confirm?.click();
    });
    await flush();

    expect(dialog()).toBeNull();
    const alert = container.querySelector('form [role="alert"]');
    expect(alert?.textContent).toContain('Only a platform owner can change this.');
    // The fee in force is untouched: the page shows the server's state, not the draft.
    expect(container.querySelector('[data-testid="fee-in-force"]')?.textContent).toBe('5%');
  });

  it('reports a failed read instead of pretending', async () => {
    respond = (call) =>
      call.url.endsWith('/billing/usage')
        ? jsonResponse(200, USAGE)
        : jsonResponse(500, { code: 'internal', message: 'boom', action: 'Try again.' });
    await mount(<PlatformFeePage client={client()} />);
    expect(container.textContent).toContain("The fee isn't available right now");
    expect(container.querySelector('form')).toBeNull();
  });
});
