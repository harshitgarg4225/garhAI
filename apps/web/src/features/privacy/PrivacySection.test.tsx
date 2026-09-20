/**
 * The Privacy section, rendered for real over a stub transport.
 *
 * What is pinned: the trail an admin reads (grouped, described, filtered, paged),
 * the refusal a member gets instead of an empty table, the DPDP export saved as a
 * file, and the erasure that cannot happen without the address typed back —
 * including the confirm step and the server's 409 surfaced whole.
 */

import { act, type ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '@garh/ui';

import { createApiClient } from '../../lib/api';
import { HttpClient } from '../../lib/http';
import { TokenStore } from '../../lib/tokens';
import { useSessionStore } from '../../stores/session';
import { describeAction, describeActor, describeEntity, groupByDay, metaPairs } from './audit';
import { PrivacySection } from './PrivacySection';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const BASE = 'http://api.test/api/v1';
const ME = {
  id: 'c0000000-0000-4000-8000-000000000003',
  email: 'asha@studio.test',
  name: 'Asha Rao',
  role: 'admin' as const,
  coaNumber: null,
};

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

function entry(over: Partial<Record<string, unknown>> = {}): Record<string, unknown> {
  return {
    id: `id-${Math.random().toString(36).slice(2)}`,
    at: '2026-09-20T09:15:00+00:00',
    action: 'export.created',
    entity: 'project',
    entityId: 'b0000000-0000-4000-8000-000000000002',
    actorId: ME.id,
    actorName: 'Asha Rao',
    meta: {},
    ...over,
  };
}

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

function text(selector = 'body'): string {
  const el = selector === 'body' ? container : container.querySelector(selector);
  return el?.textContent ?? '';
}

function byTestId(id: string): HTMLElement | null {
  const el =
    container.querySelector(`[data-testid="${id}"]`) ??
    document.querySelector(`[data-testid="${id}"]`);
  return el instanceof HTMLElement ? el : null;
}

function setValue(el: HTMLInputElement, value: string): void {
  // React tracks an input's value through its own setter, so `el.value = x` is
  // invisible to onChange; the prototype setter is the documented way around it.
  // eslint-disable-next-line @typescript-eslint/unbound-method -- applied with an explicit receiver below
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  if (setter !== undefined) Reflect.apply(setter, el, [value]);
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

/** The default transport: an admin, a two-day trail, a vocabulary. */
function respondDefault(): void {
  respond = (call) => {
    if (call.url.includes('/audit/actions')) {
      return jsonResponse(200, { actions: ['auth.login', 'export.created'] });
    }
    if (call.url.includes('/audit')) {
      if (call.url.includes('cursor=next')) {
        return jsonResponse(200, {
          items: [entry({ at: '2026-09-18T08:00:00+00:00', action: 'auth.login' })],
          nextCursor: null,
        });
      }
      return jsonResponse(200, {
        items: [
          entry({ meta: { kind: 'pdf-set', code: '[redacted]' } }),
          entry({
            at: '2026-09-19T11:00:00+00:00',
            action: 'share_link.created',
            actorName: null,
            actorId: null,
          }),
        ],
        nextCursor: 'next',
      });
    }
    return jsonResponse(404, { code: 'not_found', message: 'no', action: 'no' });
  };
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  sessionStorage.clear();
  calls = [];
  respondDefault();
  useSessionStore.setState({ user: ME, firm: null, status: 'authenticated' });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('audit helpers', () => {
  it('describes an action, and shows an unknown one as its own code', () => {
    expect(describeAction('export.created')).toBe('exported drawings');
    expect(describeAction('auth.login')).toBe('signed in');
    // The rule that matters: never invent a phrase for something new.
    expect(describeAction('inventory.reticulated')).toBe('inventory.reticulated');
  });

  it('names the actor, an erased one, and the system', () => {
    expect(describeActor(entry() as never)).toBe('Asha Rao');
    expect(describeActor(entry({ actorName: null }) as never)).toBe('Someone (account erased)');
    expect(describeActor(entry({ actorName: null, actorId: null }) as never)).toBe('The system');
  });

  it('names the entity, and groups rows by day newest first', () => {
    expect(describeEntity(entry() as never)).toBe('project b0000000');
    expect(describeEntity(entry({ entityId: null }) as never)).toBe('project');
    expect(describeEntity(entry({ entity: '' }) as never)).toBe('—');

    const groups = groupByDay([
      entry({ at: '2026-09-20T09:00:00+00:00' }),
      entry({ at: '2026-09-20T08:00:00+00:00' }),
      entry({ at: '2026-09-19T08:00:00+00:00' }),
    ] as never);
    expect(groups).toHaveLength(2);
    expect(groups[0]?.items).toHaveLength(2);
  });

  it('renders meta pairs, passing the server’s redaction through', () => {
    expect(metaPairs({ kind: 'pdf-set', code: '[redacted]', n: 3, deep: { a: 1 } })).toEqual([
      { key: 'kind', value: 'pdf-set' },
      { key: 'code', value: '[redacted]' },
      { key: 'n', value: '3' },
      { key: 'deep', value: '{"a":1}' },
    ]);
  });
});

describe('PrivacySection', () => {
  it('an admin reads the trail, grouped and described, and can page it', async () => {
    await mount(<PrivacySection client={client()} />);
    expect(calls.some((c) => c.url.includes('/audit?limit=50'))).toBe(true);
    expect(calls.some((c) => c.url.includes('/audit/actions'))).toBe(true);

    expect(container.querySelectorAll('[data-testid="audit-row"]')).toHaveLength(2);
    const list = text('[data-testid="audit-list"]');
    expect(list).toContain('Asha Rao');
    expect(list).toContain('exported drawings');
    expect(list).toContain('The system'); // the row with no actor
    expect(list).toContain('created a share link');
    expect(list).toContain('[redacted]');
    expect(list).toContain('project b0000000');

    byTestId('audit-more')?.click();
    await flush();
    expect(calls.some((c) => c.url.includes('cursor=next'))).toBe(true);
    expect(container.querySelectorAll('[data-testid="audit-row"]')).toHaveLength(3);
  });

  it('filtering by action re-reads the trail with that query', async () => {
    await mount(<PrivacySection client={client()} />);
    const select = container.querySelector('select');
    expect(select).not.toBeNull();
    // Every action the server named is offered, with its phrase beside it.
    expect(select?.textContent).toContain('signed in');
    act(() => {
      if (select instanceof HTMLSelectElement) {
        select.value = 'auth.login';
        select.dispatchEvent(new Event('change', { bubbles: true }));
      }
    });
    await flush();
    expect(calls.some((c) => c.url.includes('action=auth.login'))).toBe(true);
  });

  it('a member is told why they cannot read it, and the trail is never requested', async () => {
    useSessionStore.setState({ user: { ...ME, role: 'member' } });
    await mount(<PrivacySection client={client()} />);
    expect(byTestId('audit-admin-only')).not.toBeNull();
    expect(text()).toContain('only an admin of this practice can read it');
    expect(calls.some((c) => c.url.includes('/audit'))).toBe(false);
    // The rest of the page — their own DPDP rights — is still theirs.
    expect(byTestId('privacy-export')).not.toBeNull();
    expect(byTestId('erasure-form')).not.toBeNull();
  });

  it('a 403 from the server renders as the refusal, not an empty trail', async () => {
    respond = (call) =>
      call.url.includes('/audit/actions')
        ? jsonResponse(200, { actions: [] })
        : jsonResponse(403, {
            code: 'permission_denied',
            message: 'Only an admin can read the trail.',
            action: 'Ask an admin of your practice.',
          });
    await mount(<PrivacySection client={client()} />);
    expect(text()).toContain('Only an admin can read the trail.');
    expect(byTestId('audit-empty')).toBeNull();
  });

  it('the export is fetched and handed to the browser as a file', async () => {
    const created: string[] = [];
    const url = globalThis.URL as unknown as {
      createObjectURL?: (b: Blob) => string;
      revokeObjectURL?: (u: string) => void;
    };
    url.createObjectURL = () => {
      created.push('blob:x');
      return 'blob:x';
    };
    url.revokeObjectURL = () => undefined;
    const clicked: string[] = [];
    // eslint-disable-next-line @typescript-eslint/unbound-method -- restored verbatim below
    const realClick = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function patched(this: HTMLAnchorElement): void {
      clicked.push(this.download);
    };

    respond = (call) => {
      if (call.url.includes('/privacy/export')) {
        return jsonResponse(200, {
          generatedAt: '2026-09-20T09:20:00+00:00',
          subject: { ...ME, coaNumber: null },
          firm: { id: 'f1', role: 'admin' },
          signedInDevices: [],
          authTrail: [],
          authTrailTruncated: false,
          comments: [],
          commentsWithheld: 0,
          designActivity: { opCount: 5, projectIds: [], firstOpAt: null, lastOpAt: null, note: '' },
        });
      }
      if (call.url.includes('/audit/actions')) return jsonResponse(200, { actions: [] });
      return jsonResponse(200, { items: [], nextCursor: null });
    };

    await mount(<PrivacySection client={client()} />);
    byTestId('privacy-export')?.click();
    await flush();

    expect(calls.some((c) => c.url.includes('/privacy/export'))).toBe(true);
    expect(created).toHaveLength(1);
    expect(clicked[0]).toMatch(/^garh-my-data-\d{4}-\d{2}-\d{2}\.json$/);
    HTMLAnchorElement.prototype.click = realClick;
  });

  it('erasure needs the address typed back, then a confirm, and reports what was kept', async () => {
    const onErased = vi.fn();
    await mount(<PrivacySection client={client()} onErased={onErased} />);

    const submit = byTestId('erasure-submit') as HTMLButtonElement | null;
    expect(submit?.disabled).toBe(true);

    const input = byTestId('erasure-form')?.querySelector('input');
    expect(input).toBeInstanceOf(HTMLInputElement);
    act(() => setValue(input as HTMLInputElement, 'someone-else@studio.test'));
    await flush();
    expect((byTestId('erasure-submit') as HTMLButtonElement).disabled).toBe(true);

    act(() => setValue(input as HTMLInputElement, '  ASHA@studio.test '));
    await flush();
    expect((byTestId('erasure-submit') as HTMLButtonElement).disabled).toBe(false);

    respond = (call) => {
      if (call.url.includes('/privacy/erasure')) {
        return jsonResponse(200, {
          erased: true,
          opsAnonymised: 142,
          commentsAnonymised: 3,
          shareLinksAnonymised: 1,
          sessionsEnded: 2,
          auditEntriesRetained: 87,
        });
      }
      if (call.url.includes('/auth/logout')) return jsonResponse(204, null);
      if (call.url.includes('/audit/actions')) return jsonResponse(200, { actions: [] });
      return jsonResponse(200, { items: [], nextCursor: null });
    };

    act(() => {
      (byTestId('erasure-submit') as HTMLButtonElement).click();
    });
    await flush();
    // Nothing is deleted on the click: a confirm stands between.
    expect(calls.some((c) => c.url.includes('/privacy/erasure'))).toBe(false);
    const dialog = document.querySelector('[role="dialog"], [role="alertdialog"]');
    expect(dialog?.textContent).toContain('cannot be undone');
    expect(dialog?.textContent).toContain('with your name removed');

    const confirmButton = Array.from(
      document.querySelectorAll<HTMLButtonElement>(
        '[role="dialog"] button, [role="alertdialog"] button',
      ),
    ).find((b) => /delete my account/i.test(b.textContent ?? ''));
    act(() => confirmButton?.click());
    await flush();

    const erasure = calls.find((c) => c.url.includes('/privacy/erasure'));
    expect(erasure?.method).toBe('POST');
    expect(erasure?.body).toEqual({ confirmEmail: 'ASHA@studio.test' });
    expect(text()).toContain('142 design edits');
    expect(text()).toContain('87 audit rows are retained');
    expect(onErased).toHaveBeenCalledTimes(1);
  });

  it('a 409 from erasure is shown and the account is left alone', async () => {
    const onErased = vi.fn();
    await mount(<PrivacySection client={client()} onErased={onErased} />);
    const input = byTestId('erasure-form')?.querySelector('input');
    act(() => setValue(input as HTMLInputElement, ME.email));
    await flush();

    respond = (call) =>
      call.url.includes('/privacy/erasure')
        ? jsonResponse(409, {
            code: 'conflict',
            message: 'You are the last admin of this practice.',
            action: 'Promote another admin, then try again.',
          })
        : jsonResponse(200, { items: [], nextCursor: null });

    act(() => {
      (byTestId('erasure-submit') as HTMLButtonElement).click();
    });
    await flush();
    const confirmButton = Array.from(
      document.querySelectorAll<HTMLButtonElement>(
        '[role="dialog"] button, [role="alertdialog"] button',
      ),
    ).find((b) => /delete my account/i.test(b.textContent ?? ''));
    act(() => confirmButton?.click());
    await flush();

    expect(text()).toContain('You are the last admin of this practice.');
    expect(text()).toContain('Promote another admin');
    expect(byTestId('privacy-erased')).toBeNull();
    expect(onErased).not.toHaveBeenCalled();
  });
});
