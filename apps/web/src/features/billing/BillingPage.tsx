/**
 * BillingPage — plan, allowances, the ledger, GST details, invoices and seats, at
 * `/billing`. Also where every 402 lands: `?reason=…&kind=…` opens the page on a
 * banner that says what ran out, when it resets, and which plan would lift it, with
 * the plans catalogue right beneath.
 *
 * Reads are open to every member (an architect should see how many renders are left
 * without being an admin); every write is admin-only here AND on the server, so the
 * buttons an admin sees are the ones the API will honour. Money reads in rupees at
 * the dated rate the usage response carries; the ledger's rows keep their dollar
 * source in `title`.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type JSX } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  ConfirmDialog,
  Field,
  Input,
  ProgressBar,
  SelectField,
  useToast,
} from '@garh/ui';

import { AppShell, PageBody, PageHeader } from '../../components';
import {
  api,
  type ApiClient,
  type BillingAccount,
  type BillingAccountInput,
  type CreditEvent,
  type GstState,
  type Invoice,
  type Page,
  type Plan,
  type PlanList,
  type SeatList,
  type Subscription,
  type Usage,
} from '../../lib/api';
import { AppError } from '../../lib/errors';
import { useSessionStore } from '../../stores/session';
import { BillingLinks } from './BillingLinks';
import { formatWhen } from './markup';
import { describeRate, formatWholeInr } from './money';
import { describeOutOfCredits, readOutOfCredits } from './outOfCredits';
import { MoneyText } from './TrialUsageCard';
import { describeFee, kindLabel, splitCharge } from './usage';

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------

interface BillingData {
  readonly usage: Usage | null;
  readonly plans: PlanList | null;
  readonly subscription: Subscription | null;
  readonly states: GstState[];
  /** `null` until the firm has set one (the API answers 409 until then). */
  readonly account: BillingAccount | null;
  readonly invoices: Invoice[];
  readonly seats: SeatList | null;
  readonly events: CreditEvent[];
  readonly eventsCursor: string | null;
  readonly errors: string[];
}

const EMPTY: BillingData = {
  usage: null,
  plans: null,
  subscription: null,
  states: [],
  account: null,
  invoices: [],
  seats: null,
  events: [],
  eventsCursor: null,
  errors: [],
};

async function settle<T>(
  promise: Promise<T>,
  errors: string[],
  label: string,
  fallback: T,
  ignore?: (error: AppError) => boolean,
): Promise<T> {
  try {
    return await promise;
  } catch (err) {
    const problem = AppError.from(err);
    if (ignore?.(problem) !== true) errors.push(`${label}: ${problem.message}`);
    return fallback;
  }
}

function useBillingData(
  client: ApiClient,
  isAdmin: boolean,
): { data: BillingData; loading: boolean; reload: () => void; loadMoreEvents: () => void } {
  const [data, setData] = useState<BillingData>(EMPTY);
  const [loading, setLoading] = useState(true);
  const generation = useRef(0);

  const load = useCallback(async () => {
    const gen = (generation.current += 1);
    setLoading(true);
    const errors: string[] = [];
    const empty: Page<Invoice> = { items: [], nextCursor: null, hasMore: false };
    const emptyEvents: Page<CreditEvent> = { items: [], nextCursor: null, hasMore: false };
    const [usage, plans, subscription, states, account, invoices, seats, events] =
      await Promise.all([
        settle(client.billing.usage(), errors, 'Usage', null),
        settle(client.billing.plans(), errors, 'Plans', null),
        settle(client.billing.subscription(), errors, 'Subscription', null),
        isAdmin ? settle(client.billing.states(), errors, 'GST states', []) : Promise.resolve([]),
        isAdmin
          ? settle(
              client.billing.account(),
              errors,
              'Billing details',
              null,
              (e) => e.code === 'billing_profile_incomplete',
            )
          : Promise.resolve(null),
        settle(client.billing.invoices(), errors, 'Invoices', empty),
        settle(client.billing.seats(), errors, 'Seats', null),
        settle(client.billing.creditEvents({ limit: 20 }), errors, 'Charges', emptyEvents),
      ]);
    if (gen !== generation.current) return;
    setData({
      usage,
      plans,
      subscription,
      states,
      account,
      invoices: invoices.items,
      seats,
      events: events.items,
      eventsCursor: events.nextCursor,
      errors,
    });
    setLoading(false);
  }, [client, isAdmin]);

  useEffect(() => {
    void load();
  }, [load]);

  const loadMoreEvents = useCallback(() => {
    const cursor = data.eventsCursor;
    if (cursor === null) return;
    void client.billing
      .creditEvents({ cursor, limit: 20 })
      .then((page) => {
        setData((prev) => ({
          ...prev,
          events: [...prev.events, ...page.items],
          eventsCursor: page.nextCursor,
        }));
      })
      .catch((err: unknown) => {
        const problem = AppError.from(err);
        setData((prev) => ({ ...prev, errors: [...prev.errors, `Charges: ${problem.message}`] }));
      });
  }, [client, data.eventsCursor]);

  return { data, loading, reload: () => void load(), loadMoreEvents };
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export interface BillingPageProps {
  /** Injected by tests; the app uses the singleton. */
  readonly client?: ApiClient | undefined;
}

export function BillingPage({ client = api }: BillingPageProps): JSX.Element {
  const firm = useSessionStore((s) => s.firm);
  const user = useSessionStore((s) => s.user);
  const signOut = useSessionStore((s) => s.signOut);
  const isAdmin = user?.role === 'admin';
  const { toast } = useToast();
  const [search] = useSearchParams();
  const arrival = useMemo(() => readOutOfCredits(search), [search]);

  const { data, loading, reload, loadMoreEvents } = useBillingData(client, isAdmin);
  const [pendingPlan, setPendingPlan] = useState<Plan | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  function fail(title: string, err: unknown): void {
    const problem = AppError.from(err);
    toast({
      severity: 'fail',
      title,
      description: `${problem.message} ${problem.action}`.trim(),
      // The server's own `action` text is in the description; the button re-reads the
      // page so what it shows is the state the refusal left behind.
      action: { label: 'Reload billing', onClick: reload },
    });
  }

  async function changePlan(plan: Plan): Promise<void> {
    setBusy('plan');
    try {
      const next = await client.billing.updateSubscription({ planCode: plan.code });
      setPendingPlan(null);
      toast({
        severity: 'pass',
        title: `You're on ${next.planName} now`,
        description: `${formatWholeInr(next.monthlyChargeInr)} a month, billed on the next invoice.`,
      });
      reload();
    } catch (err) {
      setPendingPlan(null);
      fail("Couldn't change the plan", err);
    } finally {
      setBusy(null);
    }
  }

  async function saveAccount(input: BillingAccountInput): Promise<string | null> {
    setBusy('account');
    try {
      await client.billing.updateAccount(input);
      toast({ severity: 'pass', title: 'Billing details saved' });
      reload();
      return null;
    } catch (err) {
      const problem = AppError.from(err);
      return `${problem.message} ${problem.action}`.trim();
    } finally {
      setBusy(null);
    }
  }

  async function issueInvoice(): Promise<void> {
    setBusy('issue');
    try {
      const invoice = await client.billing.issueInvoice();
      toast({
        severity: 'pass',
        title: `Invoice ${invoice.invoiceNumber} issued`,
        description: `${formatWholeInr(invoice.totalInr)} including GST.`,
      });
      reload();
    } catch (err) {
      fail("Couldn't issue an invoice", err);
    } finally {
      setBusy(null);
    }
  }

  async function payInvoice(invoice: Invoice): Promise<void> {
    setBusy(`pay:${invoice.id}`);
    try {
      const checkout = await client.billing.checkout(invoice.id);
      if (checkout.provider !== 'mock') {
        toast({
          severity: 'warn',
          title: 'Live checkout is not wired into this build',
          description: `Order ${checkout.orderId} is open with ${checkout.provider}; settle it through the gateway's own checkout.`,
        });
        return;
      }
      const widget = await client.billing.mockPay(checkout.orderId);
      const settled = await client.billing.verifyPayment({
        orderId: widget.orderId,
        paymentId: widget.paymentId,
        signature: widget.signature,
      });
      toast({
        severity: 'pass',
        title: `Invoice ${settled.invoice.invoiceNumber} paid`,
        description: 'Mock checkout — no money moved. The signature was verified for real.',
      });
      reload();
    } catch (err) {
      fail("Couldn't take the payment", err);
    } finally {
      setBusy(null);
    }
  }

  const feeText = data.usage === null ? null : describeFee(data.usage);

  return (
    <AppShell
      firmName={firm?.name}
      userName={user?.name}
      onSignOut={() => void signOut()}
      renderHomeLink={({ className, children }) => (
        <Link to="/" className={className}>
          {children}
        </Link>
      )}
      headerActions={<BillingLinks client={client} />}
    >
      <PageBody className="max-w-5xl">
        <PageHeader
          title="Billing"
          description={
            feeText === null
              ? 'Plan, allowances, charges and invoices for your practice.'
              : `Plan, allowances, charges and invoices for your practice. Every charge carries a ${feeText}.`
          }
        />

        {arrival !== null ? (
          <OutOfCreditsBanner
            {...describeOutOfCredits(arrival, data.usage, data.plans?.plans ?? [])}
          />
        ) : null}

        {data.errors.length > 0 ? (
          <Card className="mb-4">
            <CardBody className="pt-4">
              <p className="text-sm text-fail" role="alert">
                Some of this page could not be loaded — {data.errors.join('; ')}
              </p>
              <Button className="mt-2" size="sm" onClick={reload}>
                Try again
              </Button>
            </CardBody>
          </Card>
        ) : null}

        <PlanSection
          plans={data.plans}
          subscription={data.subscription}
          isAdmin={isAdmin}
          loading={loading}
          onChoose={(plan) => setPendingPlan(plan)}
        />

        <AllowancesSection usage={data.usage} />

        <LedgerSection
          events={data.events}
          usage={data.usage}
          hasMore={data.eventsCursor !== null}
          onMore={loadMoreEvents}
        />

        {isAdmin ? (
          <GstSection
            account={data.account}
            states={data.states}
            busy={busy === 'account'}
            onSave={saveAccount}
          />
        ) : null}

        <InvoicesSection
          invoices={data.invoices}
          isAdmin={isAdmin}
          busy={busy}
          onIssue={() => void issueInvoice()}
          onPay={(invoice) => void payInvoice(invoice)}
        />

        <SeatsSection seats={data.seats} />
      </PageBody>

      <ConfirmDialog
        open={pendingPlan !== null}
        onOpenChange={(open) => {
          if (!open) setPendingPlan(null);
        }}
        title={pendingPlan === null ? '' : `Move to ${pendingPlan.name}?`}
        description={
          pendingPlan === null
            ? ''
            : `${formatWholeInr(pendingPlan.priceInrPerMonth)} a month plus GST, billed on the next invoice. The new allowances apply immediately; the elapsed part of this period is not prorated.`
        }
        confirmLabel={pendingPlan === null ? 'Confirm' : `Move to ${pendingPlan.name}`}
        busy={busy === 'plan'}
        onConfirm={() => {
          if (pendingPlan !== null) void changePlan(pendingPlan);
        }}
      />
    </AppShell>
  );
}

// ---------------------------------------------------------------------------
// Sections
// ---------------------------------------------------------------------------

function OutOfCreditsBanner({ title, detail }: { title: string; detail: string }): JSX.Element {
  return (
    <section
      role="status"
      aria-label="Out of credits"
      data-testid="out-of-credits"
      className="mb-4 rounded-md border border-fail-line bg-fail-soft px-4 py-3 text-sm"
    >
      <p className="font-semibold text-ink">{title}</p>
      {detail !== '' ? <p className="mt-0.5 text-ink-muted">{detail}</p> : null}
    </section>
  );
}

function PlanSection({
  plans,
  subscription,
  isAdmin,
  loading,
  onChoose,
}: {
  plans: PlanList | null;
  subscription: Subscription | null;
  isAdmin: boolean;
  loading: boolean;
  onChoose: (plan: Plan) => void;
}): JSX.Element {
  const currentCode = plans?.currentPlanCode ?? subscription?.planCode ?? null;
  const periodEnd = subscription === null ? null : formatWhen(subscription.currentPeriodEnd);
  return (
    <Card className="mb-4" aria-label="Plan">
      <CardHeader
        title="Plan"
        description={
          subscription === null
            ? loading
              ? 'Loading your plan…'
              : 'Your plan could not be loaded.'
            : `${subscription.planName} — ${formatWholeInr(subscription.monthlyChargeInr)} a month plus GST, ${subscription.seatsEntitled} editor ${subscription.seatsEntitled === 1 ? 'seat' : 'seats'}${periodEnd === null ? '' : `, period ends ${periodEnd}`}${subscription.cancelAtPeriodEnd ? ' (cancels at period end)' : ''}.`
        }
        actions={
          subscription !== null && subscription.effectivePlanCode !== subscription.planCode ? (
            <Badge tone="warn">Past due — {subscription.effectivePlanCode} allowances apply</Badge>
          ) : undefined
        }
      />
      <CardBody>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="plan-cards">
          {(plans?.plans ?? []).map((plan) => {
            const current = plan.code === currentCode;
            return (
              <div
                key={plan.code}
                className={
                  'flex flex-col gap-2 rounded-md border p-3 ' +
                  (current ? 'border-brand bg-surface-muted' : 'border-line bg-surface')
                }
                data-testid={`plan-${plan.code}`}
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="font-semibold text-ink">{plan.name}</span>
                  {current ? <Badge tone="brand">Current</Badge> : null}
                </div>
                <p className="text-lg text-ink">
                  {plan.priceInrPerMonth === 0
                    ? 'Free'
                    : `${formatWholeInr(plan.priceInrPerMonth)}/mo`}
                </p>
                <p className="text-xs text-ink-muted">{plan.summary}</p>
                <ul className="text-xs text-ink-muted">
                  <li>
                    {plan.includedEditorSeats} editor{' '}
                    {plan.includedEditorSeats === 1 ? 'seat' : 'seats'}
                    {plan.extraSeatInrPerMonth === null
                      ? ''
                      : `, extra ${formatWholeInr(plan.extraSeatInrPerMonth)}/mo`}
                  </li>
                  {plan.allowances.map((line) => (
                    <li key={line.kind}>
                      {line.allowance === null
                        ? `Unmetered ${kindLabel(line.kind).toLowerCase()}`
                        : line.allowance === 0
                          ? `No ${kindLabel(line.kind).toLowerCase()}`
                          : `${line.allowance} ${kindLabel(line.kind).toLowerCase()} a month`}
                    </li>
                  ))}
                </ul>
                {isAdmin && !current ? (
                  <Button size="sm" variant="secondary" onClick={() => onChoose(plan)}>
                    Move to {plan.name}
                  </Button>
                ) : null}
              </div>
            );
          })}
        </div>
        {!isAdmin ? (
          <p className="mt-3 text-xs text-ink-muted">A firm admin can change the plan.</p>
        ) : null}
      </CardBody>
    </Card>
  );
}

function AllowancesSection({ usage }: { usage: Usage | null }): JSX.Element {
  const periodEnd = usage === null ? null : formatWhen(usage.periodEnd);
  return (
    <Card className="mb-4" aria-label="Allowances">
      <CardHeader
        title="Allowances this period"
        description={
          periodEnd === null
            ? 'Used against what the plan includes.'
            : `Used against what the plan includes. Resets ${periodEnd}.`
        }
      />
      <CardBody>
        {usage === null ? (
          <p className="text-sm text-ink-muted">Loading usage…</p>
        ) : (
          <ul className="grid gap-3 sm:grid-cols-2" data-testid="allowances">
            {usage.lines.map((line) => {
              const label = kindLabel(line.kind);
              const detail =
                line.allowance === null
                  ? `${line.used} used, unmetered`
                  : line.allowance === 0
                    ? 'Not included on this plan'
                    : `${line.used} of ${line.allowance} used`;
              const value =
                line.allowance === null
                  ? null
                  : line.allowance === 0
                    ? 100
                    : Math.min(100, Math.round((line.used / line.allowance) * 100));
              return (
                <li key={line.kind} data-testid={`allowance-${line.kind}`}>
                  <ProgressBar value={value} label={label} detail={detail} />
                </li>
              );
            })}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}

function LedgerSection({
  events,
  usage,
  hasMore,
  onMore,
}: {
  events: CreditEvent[];
  usage: Usage | null;
  hasMore: boolean;
  onMore: () => void;
}): JSX.Element {
  const spend = usage?.spend ?? null;
  const rate = spend === null ? null : describeRate(spend.usdInrRate, spend.usdInrRateAsOf);
  return (
    <Card className="mb-4" aria-label="Charges">
      <CardHeader
        title="Charges"
        description={
          rate === null
            ? 'Every metered job, newest first. Provider cost and the platform fee are separate columns.'
            : `Every metered job, newest first, in rupees at ${rate}; hover a figure for the dollars. Provider cost and the platform fee are separate columns.`
        }
      />
      <CardBody>
        {events.length === 0 ? (
          <p className="text-sm text-ink-muted">No charges yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="ledger">
              <thead className="text-left text-xs text-ink-muted">
                <tr>
                  <th className="py-1 pr-3">When</th>
                  <th className="py-1 pr-3">What</th>
                  <th className="py-1 pr-3">Provider</th>
                  <th className="py-1 pr-3 text-right">Cost</th>
                  <th className="py-1 pr-3 text-right">Fee</th>
                  <th className="py-1 pr-3 text-right">Charged</th>
                  <th className="py-1">Status</th>
                </tr>
              </thead>
              <tbody>
                {events.map((event) => {
                  const parts =
                    spend === null
                      ? null
                      : splitCharge(event.costMicros, event.chargedMicros, spend);
                  const usd = `$${(event.costMicros / 1_000_000).toFixed(6)} + $${(
                    Math.max(0, event.chargedMicros - event.costMicros) / 1_000_000
                  ).toFixed(6)} = $${(event.chargedMicros / 1_000_000).toFixed(6)}`;
                  const source = rate === null ? null : `${usd} at ${rate}`;
                  return (
                    <tr
                      key={event.id}
                      className={
                        'border-t border-line ' +
                        (event.refundedAt !== null ? 'text-ink-muted' : '')
                      }
                      data-testid={`ledger-${event.kind}`}
                    >
                      <td className="py-1.5 pr-3 whitespace-nowrap">
                        {formatWhen(event.createdAt) ?? event.createdAt}
                      </td>
                      <td className="py-1.5 pr-3">
                        {kindLabel(event.kind)}
                        {event.qty > 1 ? ` ×${event.qty}` : ''}
                        {event.detail !== '' && event.refundedAt === null ? (
                          <span className="text-xs text-ink-muted"> · {event.detail}</span>
                        ) : null}
                      </td>
                      <td className="py-1.5 pr-3">{event.provider || '—'}</td>
                      <td className="py-1.5 pr-3 text-right">
                        <MoneyText text={parts?.cost ?? usd} source={source} />
                      </td>
                      <td className="py-1.5 pr-3 text-right">
                        <MoneyText
                          text={parts?.fee ?? ''}
                          source={source === null ? null : `${event.markupBps / 100}% of cost`}
                        />
                      </td>
                      <td className="py-1.5 pr-3 text-right">
                        <MoneyText text={parts?.charged ?? ''} source={source} />
                      </td>
                      <td className="py-1.5">
                        {event.refundedAt !== null ? (
                          <Badge tone="info">
                            Refunded
                            {event.detail.startsWith('refunded: ')
                              ? ` — ${event.detail.slice('refunded: '.length)}`
                              : ''}
                          </Badge>
                        ) : event.chargedMicros === 0 ? (
                          <Badge tone="neutral">Free</Badge>
                        ) : (
                          <Badge tone="pass">Charged</Badge>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {hasMore ? (
          <Button className="mt-3" size="sm" onClick={onMore}>
            Show older charges
          </Button>
        ) : null}
      </CardBody>
    </Card>
  );
}

interface GstDraft {
  legalName: string;
  stateCode: string;
  gstin: string;
  addressLine: string;
  city: string;
  postalCode: string;
  billingEmail: string;
}

function draftFrom(account: BillingAccount | null): GstDraft {
  return {
    legalName: account?.legalName ?? '',
    stateCode: account?.stateCode ?? '',
    gstin: account?.gstin ?? '',
    addressLine: account?.addressLine ?? '',
    city: account?.city ?? '',
    postalCode: account?.postalCode ?? '',
    billingEmail: account?.billingEmail ?? '',
  };
}

function GstSection({
  account,
  states,
  busy,
  onSave,
}: {
  account: BillingAccount | null;
  states: GstState[];
  busy: boolean;
  onSave: (input: BillingAccountInput) => Promise<string | null>;
}): JSX.Element {
  const [draft, setDraft] = useState<GstDraft>(() => draftFrom(account));
  const [error, setError] = useState<string | null>(null);
  const loadedFor = useRef<BillingAccount | null>(account);
  useEffect(() => {
    if (loadedFor.current !== account) {
      loadedFor.current = account;
      setDraft(draftFrom(account));
    }
  }, [account]);

  const set = (key: keyof GstDraft) => (value: string) => {
    setDraft((prev) => ({ ...prev, [key]: value }));
    setError(null);
  };

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (draft.legalName.trim() === '') {
      setError('The legal name is what goes on the tax invoice — it cannot be blank.');
      return;
    }
    if (draft.stateCode === '') {
      setError('Pick the place of supply: it decides CGST+SGST versus IGST.');
      return;
    }
    const problem = await onSave({
      legalName: draft.legalName.trim(),
      stateCode: draft.stateCode,
      gstin: draft.gstin.trim() === '' ? null : draft.gstin.trim().toUpperCase(),
      addressLine: draft.addressLine.trim(),
      city: draft.city.trim(),
      postalCode: draft.postalCode.trim(),
      billingEmail: draft.billingEmail.trim(),
    });
    setError(problem);
  }

  return (
    <Card className="mb-4" aria-label="GST details">
      <CardHeader
        title="GST details"
        description={
          account === null
            ? 'Needed before an invoice can be issued: the legal name and state go on it, the GSTIN gives you input credit.'
            : `Invoices are issued to ${account.legalName}, ${account.stateName || account.stateCode}.`
        }
      />
      <CardBody>
        <form onSubmit={(e) => void submit(e)} noValidate aria-label="GST details">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Legal name" required>
              {({ id, describedBy, invalid }) => (
                <Input
                  id={id}
                  name="legalName"
                  aria-describedby={describedBy}
                  invalid={invalid}
                  value={draft.legalName}
                  onChange={(e) => set('legalName')(e.target.value)}
                />
              )}
            </Field>
            <SelectField
              label="Place of supply (state)"
              value={draft.stateCode}
              onValueChange={set('stateCode')}
              options={[
                { value: '', label: 'Choose a state' },
                ...states.map((state) => ({
                  value: state.code,
                  label: `${state.name} (${state.code})`,
                })),
              ]}
            />
            <Field label="GSTIN" hint="Leave blank if the practice is not registered.">
              {({ id, describedBy, invalid }) => (
                <Input
                  id={id}
                  name="gstin"
                  aria-describedby={describedBy}
                  invalid={invalid}
                  value={draft.gstin}
                  onChange={(e) => set('gstin')(e.target.value)}
                  autoComplete="off"
                />
              )}
            </Field>
            <Field label="Billing email">
              {({ id, describedBy, invalid }) => (
                <Input
                  id={id}
                  name="billingEmail"
                  type="email"
                  aria-describedby={describedBy}
                  invalid={invalid}
                  value={draft.billingEmail}
                  onChange={(e) => set('billingEmail')(e.target.value)}
                />
              )}
            </Field>
            <Field label="Address">
              {({ id, describedBy, invalid }) => (
                <Input
                  id={id}
                  name="addressLine"
                  aria-describedby={describedBy}
                  invalid={invalid}
                  value={draft.addressLine}
                  onChange={(e) => set('addressLine')(e.target.value)}
                />
              )}
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="City">
                {({ id, describedBy, invalid }) => (
                  <Input
                    id={id}
                    name="city"
                    aria-describedby={describedBy}
                    invalid={invalid}
                    value={draft.city}
                    onChange={(e) => set('city')(e.target.value)}
                  />
                )}
              </Field>
              <Field label="PIN code">
                {({ id, describedBy, invalid }) => (
                  <Input
                    id={id}
                    name="postalCode"
                    aria-describedby={describedBy}
                    invalid={invalid}
                    inputMode="numeric"
                    value={draft.postalCode}
                    onChange={(e) => set('postalCode')(e.target.value)}
                  />
                )}
              </Field>
            </div>
          </div>
          {error !== null ? (
            <p className="mt-2 text-sm text-fail" role="alert">
              {error}
            </p>
          ) : null}
          <div className="mt-3">
            <Button type="submit" variant="primary" loading={busy} loadingLabel="Saving">
              Save GST details
            </Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}

function InvoicesSection({
  invoices,
  isAdmin,
  busy,
  onIssue,
  onPay,
}: {
  invoices: Invoice[];
  isAdmin: boolean;
  busy: string | null;
  onIssue: () => void;
  onPay: (invoice: Invoice) => void;
}): JSX.Element {
  return (
    <Card className="mb-4" aria-label="Invoices">
      <CardHeader
        title="Invoices"
        description="Tax invoices for the plan, one per billing period, with GST split by place of supply."
        actions={
          isAdmin ? (
            <Button size="sm" onClick={onIssue} loading={busy === 'issue'} loadingLabel="Issuing">
              Issue this period&apos;s invoice
            </Button>
          ) : undefined
        }
      />
      <CardBody>
        {invoices.length === 0 ? (
          <p className="text-sm text-ink-muted">No invoices yet.</p>
        ) : (
          <ul className="divide-y divide-line" data-testid="invoices">
            {invoices.map((invoice) => (
              <li
                key={invoice.id}
                className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2 text-sm"
                data-testid={`invoice-${invoice.status}`}
              >
                <span className="font-medium text-ink">{invoice.invoiceNumber}</span>
                <span className="text-ink-muted">issued {invoice.issuedOn}</span>
                <span className="text-ink">
                  {formatWholeInr(invoice.totalInr)}
                  <span className="text-xs text-ink-muted">
                    {' '}
                    ({formatWholeInr(invoice.taxableInr)} +{' '}
                    {invoice.interstate
                      ? `IGST ${formatWholeInr(invoice.igstInr)}`
                      : `CGST ${formatWholeInr(invoice.cgstInr)} + SGST ${formatWholeInr(invoice.sgstInr)}`}
                    )
                  </span>
                </span>
                <Badge tone={invoice.status === 'paid' ? 'pass' : 'warn'}>
                  {invoice.status === 'paid'
                    ? `Paid${invoice.paidAt === null ? '' : ` ${formatWhen(invoice.paidAt) ?? ''}`}`
                    : invoice.status}
                </Badge>
                {isAdmin && invoice.status !== 'paid' ? (
                  <Button
                    size="sm"
                    variant="primary"
                    onClick={() => onPay(invoice)}
                    loading={busy === `pay:${invoice.id}`}
                    loadingLabel="Paying"
                  >
                    Pay
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}

function SeatsSection({ seats }: { seats: SeatList | null }): JSX.Element {
  return (
    <Card aria-label="Seats">
      <CardHeader
        title="Seats"
        description={
          seats === null
            ? 'Who holds an editor seat.'
            : `${seats.editorsUsed} of ${seats.entitled} editor ${seats.entitled === 1 ? 'seat' : 'seats'} assigned, ${seats.available} free; ${seats.viewersUsed} viewer ${seats.viewersUsed === 1 ? 'seat' : 'seats'} (viewers are free).`
        }
      />
      <CardBody>
        {seats === null || seats.seats.length === 0 ? (
          <p className="text-sm text-ink-muted">
            No seats assigned yet. Seats are assigned from the Team page once the practice has more
            than one member.
          </p>
        ) : (
          <ul className="text-sm" data-testid="seats">
            {seats.seats.map((seat) => (
              <li key={seat.id} className="flex gap-3 py-1">
                <span className="font-mono text-xs text-ink-muted">{seat.userId.slice(0, 8)}</span>
                <span className="text-ink">{seat.seatType}</span>
                <span className="text-ink-muted">
                  since {formatWhen(seat.createdAt) ?? seat.createdAt}
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}
