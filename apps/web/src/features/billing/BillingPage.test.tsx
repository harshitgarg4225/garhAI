/**
 * The Billing page, rendered for real over a stub transport.
 *
 * Pinned: every section from one set of wire fixtures (plan, cards, allowances, the
 * ledger in rupees with a refund, GST form prefilled, an invoice with its GST split,
 * seats); the out-of-credits banner on arrival from a 402; the admin writes with their
 * exact request bodies (plan change through the confirm, GST save, issue, the
 * checkout → mock widget → verify sequence); and a member seeing no write at all.
 */

import { act, type ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { ToastProvider } from '@garh/ui';

import { createApiClient } from '../../lib/api';
import { HttpClient } from '../../lib/http';
import { TokenStore } from '../../lib/tokens';
import { useSessionStore } from '../../stores/session';
import { BillingPage } from './BillingPage';

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

const ADMIN = {
  id: '11111111-1111-4111-8111-111111111111',
  email: 'asha@studio-one.example',
  name: 'Asha Rao',
  role: 'admin' as const,
  coaNumber: null,
};
const FIRM = {
  id: '22222222-2222-4222-8222-222222222222',
  name: 'Studio One',
  logoUrl: null,
  settings: {},
};

const USAGE = {
  planCode: 'studio',
  effectivePlanCode: 'studio',
  periodStart: '2026-09-01T00:00:00+00:00',
  periodEnd: '2026-10-01T00:00:00+00:00',
  lines: [
    { kind: 'solver', used: 2, allowance: 150, remaining: 148 },
    { kind: 'render', used: 0, allowance: 100, remaining: 100 },
    { kind: 'llm', used: 3, allowance: 1500, remaining: 1497 },
    { kind: 'export', used: 1, allowance: 100, remaining: 99, enforced: false },
  ],
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
    usdInrRate: '84.00',
    usdInrRateAsOf: '2026-09-01',
  },
};

const PLANS = {
  currentPlanCode: 'studio',
  plans: [
    {
      code: 'free',
      name: 'Free',
      priceInrPerMonth: 0,
      includedEditorSeats: 1,
      extraSeatInrPerMonth: null,
      summary: 'One architect.',
      allowances: [
        { kind: 'solver', allowance: 10 },
        { kind: 'export', allowance: 0 },
      ],
    },
    {
      code: 'studio',
      name: 'Studio',
      priceInrPerMonth: 4999,
      includedEditorSeats: 3,
      extraSeatInrPerMonth: 1499,
      summary: 'A small practice.',
      allowances: [
        { kind: 'solver', allowance: 150 },
        { kind: 'export', allowance: 100 },
      ],
    },
    {
      code: 'practice',
      name: 'Practice',
      priceInrPerMonth: 14999,
      includedEditorSeats: 10,
      extraSeatInrPerMonth: 1299,
      summary: 'Ten architects.',
      allowances: [
        { kind: 'solver', allowance: 600 },
        { kind: 'export', allowance: null },
      ],
    },
  ],
};

const SUBSCRIPTION = {
  planCode: 'studio',
  planName: 'Studio',
  effectivePlanCode: 'studio',
  status: 'active',
  currentPeriodStart: '2026-09-01T00:00:00+00:00',
  currentPeriodEnd: '2026-10-01T00:00:00+00:00',
  extraSeats: 0,
  seatsEntitled: 3,
  cancelAtPeriodEnd: false,
  monthlyChargeInr: 4999,
  provider: 'mock',
};

const STATES = [
  { code: '27', name: 'Maharashtra' },
  { code: '29', name: 'Karnataka' },
];

const ACCOUNT = {
  legalName: 'Studio One LLP',
  gstin: '29ABCDE1234F1Z5',
  stateCode: '29',
  stateName: 'Karnataka',
  addressLine: '12 MG Road',
  city: 'Bengaluru',
  postalCode: '560001',
  billingEmail: 'accounts@studio-one.example',
};

const INVOICE = {
  id: '33333333-3333-4333-8333-333333333333',
  invoiceNumber: 'GARH/26-27/0001',
  status: 'issued',
  issuedOn: '2026-09-07',
  periodStart: '2026-09-01T00:00:00+00:00',
  periodEnd: '2026-10-01T00:00:00+00:00',
  supplierLegalName: 'Garh Technologies Private Limited',
  supplierGstin: '29AAAAA0000A1Z5',
  supplierStateCode: '29',
  supplierAddress: 'Bengaluru',
  customerLegalName: 'Studio One LLP',
  customerGstin: '29ABCDE1234F1Z5',
  customerAddress: '12 MG Road, Bengaluru 560001',
  placeOfSupplyCode: '29',
  placeOfSupply: 'Karnataka',
  interstate: false,
  currency: 'INR',
  ratePercentX100: 1800,
  taxableInr: 4999,
  cgstInr: 450,
  sgstInr: 450,
  igstInr: 0,
  taxTotalInr: 900,
  totalInr: 5899,
  totalInWords: 'Rupees five thousand eight hundred ninety-nine only',
  lines: [],
  paidAt: null,
};

const SEATS = {
  entitled: 3,
  editorsUsed: 1,
  viewersUsed: 0,
  available: 2,
  seats: [
    {
      id: '44444444-4444-4444-8444-444444444444',
      userId: ADMIN.id,
      seatType: 'editor',
      assignedBy: null,
      createdAt: '2026-09-01T10:00:00+00:00',
    },
  ],
};

const EVENTS = {
  items: [
    {
      id: '55555555-5555-4555-8555-555555555555',
      kind: 'llm',
      qty: 1,
      costMicros: 1_000_000,
      markupBps: 500,
      chargedMicros: 1_050_000,
      provider: 'anthropic',
      detail: 'claude-opus-5',
      jobId: null,
      refundedAt: null,
      createdAt: '2026-09-07T09:00:00+00:00',
    },
    {
      id: '66666666-6666-4666-8666-666666666666',
      kind: 'solver',
      qty: 1,
      costMicros: 0,
      markupBps: 500,
      chargedMicros: 0,
      provider: 'local',
      detail: 'refunded: failed',
      jobId: '77777777-7777-4777-8777-777777777777',
      refundedAt: '2026-09-07T08:00:00+00:00',
      createdAt: '2026-09-07T07:59:00+00:00',
    },
  ],
  nextCursor: null,
  hasMore: false,
};

let calls: Call[];
let respond: (call: Call) => Response | null;
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
      return Promise.resolve(respond(call) ?? defaultRespond(call));
    },
  });
  return createApiClient(http);
}

/** The happy fixtures. Tests override `respond` for the calls they care about. */
function defaultRespond(call: Call): Response {
  const path = call.url.replace(BASE, '').split('?')[0] ?? '';
  if (call.method === 'GET') {
    if (path === '/billing/usage') return jsonResponse(200, USAGE);
    if (path === '/billing/plans') return jsonResponse(200, PLANS);
    if (path === '/billing/subscription') return jsonResponse(200, SUBSCRIPTION);
    if (path === '/billing/states') return jsonResponse(200, STATES);
    if (path === '/billing/account') return jsonResponse(200, ACCOUNT);
    if (path === '/billing/invoices') {
      return jsonResponse(200, { items: [INVOICE], nextCursor: null, hasMore: false });
    }
    if (path === '/billing/seats') return jsonResponse(200, SEATS);
    if (path === '/billing/credit-events') return jsonResponse(200, EVENTS);
    if (path === '/admin/billing/markup') {
      return jsonResponse(200, { percent: '5', bps: 500, source: 'default', canSet: false });
    }
  }
  return jsonResponse(404, { code: 'not_found', message: 'no such route', action: 'no' });
}

async function flush(): Promise<void> {
  for (let i = 0; i < 8; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function mount(element: ReactElement, path = '/billing'): Promise<void> {
  act(() => {
    root.render(
      <ToastProvider>
        <MemoryRouter initialEntries={[path]}>{element}</MemoryRouter>
      </ToastProvider>,
    );
  });
  await flush();
}

function buttons(text: string): HTMLButtonElement[] {
  return Array.from(document.querySelectorAll('button')).filter((b) =>
    (b.textContent ?? '').includes(text),
  );
}

async function click(button: HTMLButtonElement | undefined): Promise<void> {
  expect(button).toBeDefined();
  act(() => {
    button?.click();
  });
  await flush();
}

function setInput(el: Element | null, value: string): void {
  if (!(el instanceof HTMLInputElement)) throw new Error('no input');
  // eslint-disable-next-line @typescript-eslint/unbound-method -- called with an explicit receiver below
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  setter?.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

function setSelect(el: Element | null, value: string): void {
  if (!(el instanceof HTMLSelectElement)) throw new Error('no select');
  el.value = value;
  el.dispatchEvent(new Event('change', { bubbles: true }));
}

function signIn(role: 'admin' | 'member'): void {
  useSessionStore.setState({
    status: 'authenticated',
    user: { ...ADMIN, role },
    firm: FIRM,
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  sessionStorage.clear();
  calls = [];
  respond = () => null;
  signIn('admin');
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  useSessionStore.setState({ status: 'anonymous', user: null, firm: null });
});

describe('BillingPage', () => {
  it('shows plan, allowances, the ledger in rupees, GST details, invoices and seats', async () => {
    await mount(<BillingPage client={client()} />);
    const text = container.textContent ?? '';

    expect(text).toContain('Studio — ₹4,999 a month plus GST, 3 editor seats');
    expect(container.querySelector('[data-testid="plan-studio"]')?.textContent).toContain(
      'Current',
    );
    expect(container.querySelector('[data-testid="plan-free"]')?.textContent).toContain(
      'No exports',
    );
    expect(container.querySelector('[data-testid="plan-practice"]')?.textContent).toContain(
      'Unmetered exports',
    );
    expect(text).toContain('Every charge carries a 5% platform fee');

    expect(container.querySelector('[data-testid="allowance-solver"]')?.textContent).toContain(
      '2 of 150 used',
    );
    // A kind the API does not refuse over says so — never a wall that is not there.
    expect(container.querySelector('[data-testid="allowance-export"]')?.textContent).toContain(
      '1 of 100 used (not enforced yet)',
    );

    const llm = container.querySelector('[data-testid="ledger-llm"]');
    expect(llm?.textContent).toContain('Copilot calls');
    expect(llm?.textContent).toContain('claude-opus-5');
    expect(llm?.textContent).toContain('₹84.00'); // cost
    expect(llm?.textContent).toContain('₹4.20'); // the fee, separated
    expect(llm?.textContent).toContain('₹88.20'); // charged
    expect(llm?.textContent).toContain('Charged');
    const titles = Array.from(llm?.querySelectorAll('[title]') ?? []).map((el) =>
      el.getAttribute('title'),
    );
    expect(titles.some((t) => t?.startsWith('$1.000000 + $0.050000 = $1.050000 at ₹84.00'))).toBe(
      true,
    );
    const solver = container.querySelector('[data-testid="ledger-solver"]');
    expect(solver?.textContent).toContain('Refunded — failed');

    const legal = container.querySelector('form[aria-label="GST details"] input[name="legalName"]');
    expect(legal).toBeInstanceOf(HTMLInputElement);
    expect((legal as HTMLInputElement).value).toBe('Studio One LLP');

    const invoice = container.querySelector('[data-testid="invoice-issued"]');
    expect(invoice?.textContent).toContain('GARH/26-27/0001');
    expect(invoice?.textContent).toContain('₹5,899');
    expect(invoice?.textContent).toContain('CGST ₹450 + SGST ₹450');
    expect(buttons('Pay')).toHaveLength(1);

    expect(text).toContain('1 of 3 editor seats assigned, 2 free');
    expect(container.querySelector('[data-testid="out-of-credits"]')).toBeNull();
  });

  it('opens on the reason a 402 sent it, with the plans right beneath', async () => {
    respond = (call) =>
      call.url.includes('/billing/usage')
        ? jsonResponse(200, {
            ...USAGE,
            planCode: 'free',
            effectivePlanCode: 'free',
            lines: [{ kind: 'solver', used: 10, allowance: 10, remaining: 0 }],
          })
        : null;
    await mount(<BillingPage client={client()} />, '/billing?reason=quota_exceeded&kind=solver');
    const banner = container.querySelector('[data-testid="out-of-credits"]');
    expect(banner?.textContent).toContain("You're out of generations for this period.");
    expect(banner?.textContent).toContain('10 of 10 used this period.');
    expect(banner?.textContent).toContain('resets on 01-10-2026');
    expect(banner?.textContent).toContain('Studio includes 150 generations for ₹4,999 a month.');
    expect(container.querySelector('[data-testid="plan-cards"]')).not.toBeNull();
  });

  it('changes the plan through a confirm that names the price, and PUTs the code', async () => {
    respond = (call) =>
      call.method === 'PUT' && call.url.endsWith('/billing/subscription')
        ? jsonResponse(200, {
            ...SUBSCRIPTION,
            planCode: 'practice',
            planName: 'Practice',
            effectivePlanCode: 'practice',
            monthlyChargeInr: 14999,
            seatsEntitled: 10,
          })
        : null;
    await mount(<BillingPage client={client()} />);
    await click(buttons('Move to Practice')[0]);

    const dialog = document.querySelector('[role="dialog"], [role="alertdialog"]');
    expect(dialog?.textContent).toContain('Move to Practice?');
    expect(dialog?.textContent).toContain('₹14,999 a month plus GST');
    expect(dialog?.textContent).toContain('not prorated');
    const confirm = Array.from(dialog?.querySelectorAll('button') ?? []).find(
      (b) => (b.textContent ?? '').trim() === 'Move to Practice',
    );
    await click(confirm);

    const put = calls.find((c) => c.method === 'PUT' && c.url.endsWith('/billing/subscription'));
    expect(put?.body).toEqual({ planCode: 'practice' });
    expect(document.body.textContent).toContain("You're on Practice now");
  });

  it('starts the GST form empty when the firm has none, and PUTs what the admin enters', async () => {
    respond = (call) => {
      if (call.method === 'GET' && call.url.endsWith('/billing/account')) {
        return jsonResponse(409, {
          code: 'billing_profile_incomplete',
          message: "This firm hasn't set up its billing details yet.",
          action: 'Add them under Billing.',
        });
      }
      if (call.method === 'PUT' && call.url.endsWith('/billing/account')) {
        return jsonResponse(200, {
          ...ACCOUNT,
          legalName: 'Meera Iyer Architects',
          stateCode: '27',
        });
      }
      return null;
    };
    await mount(<BillingPage client={client()} />);
    // A 409 for "no profile yet" is not an error the page reports.
    expect(container.querySelector('[role="alert"]')).toBeNull();

    const form = container.querySelector('form[aria-label="GST details"]');
    expect(form).not.toBeNull();
    expect((form?.querySelector('input[name="legalName"]') as HTMLInputElement).value).toBe('');

    // Submit empty: refused beside the form, no request.
    await click(buttons('Save GST details')[0]);
    expect(form?.querySelector('[role="alert"]')?.textContent).toContain('legal name');
    expect(calls.filter((c) => c.method === 'PUT')).toHaveLength(0);

    act(() => {
      setInput(form?.querySelector('input[name="legalName"]') ?? null, 'Meera Iyer Architects');
      setSelect(form?.querySelector('select') ?? null, '27');
      setInput(form?.querySelector('input[name="gstin"]') ?? null, '27abcde1234f1z5');
    });
    await flush();
    await click(buttons('Save GST details')[0]);

    const put = calls.find((c) => c.method === 'PUT' && c.url.endsWith('/billing/account'));
    expect(put?.body).toEqual({
      legalName: 'Meera Iyer Architects',
      stateCode: '27',
      gstin: '27ABCDE1234F1Z5',
      addressLine: '',
      city: '',
      postalCode: '',
      billingEmail: '',
    });
    expect(document.body.textContent).toContain('Billing details saved');
  });

  it('issues an invoice, then pays it through checkout → mock widget → verify', async () => {
    let issued = false;
    respond = (call) => {
      const path = call.url.replace(BASE, '').split('?')[0];
      if (call.method === 'POST' && path === '/billing/invoices') {
        issued = true;
        return jsonResponse(201, INVOICE);
      }
      if (call.method === 'GET' && path === '/billing/invoices') {
        return jsonResponse(200, {
          items: issued ? [INVOICE] : [],
          nextCursor: null,
          hasMore: false,
        });
      }
      if (call.method === 'POST' && path === `/billing/invoices/${INVOICE.id}/checkout`) {
        return jsonResponse(200, {
          invoiceId: INVOICE.id,
          invoiceNumber: INVOICE.invoiceNumber,
          provider: 'mock',
          orderId: 'order_abc',
          amountInr: 5899,
          amountPaise: 589900,
          currency: 'INR',
          keyId: '',
        });
      }
      if (call.method === 'POST' && path === '/billing/payments/mock') {
        return jsonResponse(200, {
          orderId: 'order_abc',
          paymentId: 'pay_def',
          signature: 'f'.repeat(64),
          provider: 'mock',
        });
      }
      if (call.method === 'POST' && path === '/billing/payments/verify') {
        return jsonResponse(200, {
          payment: {
            id: '88888888-8888-4888-8888-888888888888',
            invoiceId: INVOICE.id,
            provider: 'mock',
            providerOrderId: 'order_abc',
            providerPaymentId: 'pay_def',
            status: 'captured',
            amountInr: 5899,
            currency: 'INR',
            signatureVerified: true,
          },
          invoice: { ...INVOICE, status: 'paid', paidAt: '2026-09-07T10:00:00+00:00' },
        });
      }
      return null;
    };
    await mount(<BillingPage client={client()} />);
    expect(container.textContent).toContain('No invoices yet.');

    await click(buttons("Issue this period's invoice")[0]);
    expect(document.body.textContent).toContain('Invoice GARH/26-27/0001 issued');
    expect(container.querySelector('[data-testid="invoice-issued"]')).not.toBeNull();

    await click(buttons('Pay')[0]);
    const posts = calls.filter((c) => c.method === 'POST').map((c) => c.url.replace(BASE, ''));
    expect(posts).toEqual([
      '/billing/invoices',
      `/billing/invoices/${INVOICE.id}/checkout`,
      '/billing/payments/mock',
      '/billing/payments/verify',
    ]);
    const verify = calls.find((c) => c.url.endsWith('/billing/payments/verify'));
    expect(verify?.body).toEqual({
      orderId: 'order_abc',
      paymentId: 'pay_def',
      signature: 'f'.repeat(64),
    });
    expect(document.body.textContent).toContain('Invoice GARH/26-27/0001 paid');
  });

  it('shows the server refusal when an invoice cannot be issued', async () => {
    respond = (call) =>
      call.method === 'POST' && call.url.endsWith('/billing/invoices')
        ? jsonResponse(503, {
            code: 'billing_unavailable',
            message: 'This deployment has no supplier GSTIN configured.',
            action: 'Set BILLING_SUPPLIER_GSTIN.',
          })
        : null;
    await mount(<BillingPage client={client()} />);
    await click(buttons("Issue this period's invoice")[0]);
    expect(document.body.textContent).toContain("Couldn't issue an invoice");
    expect(document.body.textContent).toContain('no supplier GSTIN configured');
  });

  it('gives a member every read and no write', async () => {
    signIn('member');
    await mount(<BillingPage client={client()} />);
    expect(container.textContent).toContain('Studio — ₹4,999 a month');
    expect(container.textContent).toContain('A firm admin can change the plan.');
    expect(buttons('Move to')).toHaveLength(0);
    expect(container.querySelector('form[aria-label="GST details"]')).toBeNull();
    expect(buttons("Issue this period's invoice")).toHaveLength(0);
    expect(buttons('Pay')).toHaveLength(0);
    // ...and never asked for the admin-only reads that would 403.
    expect(calls.some((c) => c.url.endsWith('/billing/account'))).toBe(false);
    expect(calls.some((c) => c.url.endsWith('/billing/states'))).toBe(false);
  });

  it('reports what could not be loaded instead of rendering zeros', async () => {
    respond = (call) =>
      call.url.endsWith('/billing/seats')
        ? jsonResponse(500, { code: 'internal_error', message: 'boom', action: 'Try again.' })
        : null;
    await mount(<BillingPage client={client()} />);
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('Seats: boom');
    expect(container.textContent).toContain('Studio — ₹4,999 a month');
  });
});
