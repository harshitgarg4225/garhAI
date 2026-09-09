/**
 * PlatformFeePage — the owner's one knob: the percentage on every dollar of credit.
 *
 * Three things, in reading order: the fee in force (and who set it, when), the form to
 * change it (owners only — the server's `canSet` decides what renders here, its 403
 * decides for real), and what an architect's usage card says right now. The confirm
 * says the one thing an owner must not be surprised by: the change applies to the NEXT
 * charge, and rows already recorded keep the fee they were charged at.
 *
 * Reachable at `/platform/fee`. The dashboard shows the entry only when `canSet` is
 * true; the route itself renders read-only for everyone else rather than 404ing, so a
 * shared link does not pretend the page does not exist.
 */

import { useState, type FormEvent, type JSX } from 'react';
import { Link } from 'react-router-dom';

import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  ConfirmDialog,
  Field,
  Input,
  useToast,
} from '@garh/ui';

import { AppShell, PageBody, PageHeader } from '../../components';
import { api, type ApiClient } from '../../lib/api';
import { AppError } from '../../lib/errors';
import { useSessionStore } from '../../stores/session';
import { describeChange, describeSetBy, isUnchanged, parsePercentInput } from './markup';
import { describeFee, describeSpendBreakdown } from './usage';
import { usePlatformFee } from './usePlatformFee';
import { useUsage } from './useUsage';

export interface PlatformFeePageProps {
  /** Injected by tests; the app uses the singleton. */
  readonly client?: ApiClient | undefined;
}

export function PlatformFeePage({ client = api }: PlatformFeePageProps): JSX.Element {
  const firm = useSessionStore((s) => s.firm);
  const user = useSessionStore((s) => s.user);
  const signOut = useSessionStore((s) => s.signOut);
  const { toast } = useToast();

  const fee = usePlatformFee(client);
  const [savedCount, setSavedCount] = useState(0);
  const usage = useUsage(savedCount, client);

  const [draft, setDraft] = useState('');
  const [touched, setTouched] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const parsed = parsePercentInput(draft);
  const unchanged = parsed.ok && fee.markup !== null && isUnchanged(fee.markup, parsed);
  const fieldError = !touched ? undefined : !parsed.ok ? parsed.reason : undefined;

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    setTouched(true);
    setSaveError(null);
    if (!parsed.ok || unchanged) return;
    setConfirming(true);
  }

  async function confirm(): Promise<void> {
    if (!parsed.ok) return;
    setSaving(true);
    try {
      const next = await fee.save(parsed.percent);
      setConfirming(false);
      setDraft('');
      setTouched(false);
      setSavedCount((n) => n + 1);
      toast({
        severity: 'pass',
        title: `Platform fee is now ${next.percent}%`,
        description: 'Applies from the next metered charge. Earlier charges keep their fee.',
      });
    } catch (err) {
      const problem = AppError.from(err);
      setConfirming(false);
      setSaveError(`${problem.message} ${problem.action}`.trim());
    } finally {
      setSaving(false);
    }
  }

  const markup = fee.markup;
  const change = parsed.ok ? describeChange(parsed.percent) : null;

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
    >
      <PageBody className="max-w-3xl">
        <PageHeader
          title="Platform fee"
          description="The percentage added to provider cost on every metered charge, for every firm on this deployment."
          actions={
            <Link to="/billing" className="text-sm text-brand-ink underline underline-offset-2">
              Billing
            </Link>
          }
        />

        {fee.error !== null && markup === null ? (
          <Card className="mb-4">
            <CardBody className="pt-4">
              <p className="text-sm text-fail" role="alert">
                The fee isn&apos;t available right now — {fee.error.message}
              </p>
              <Button className="mt-2" size="sm" onClick={() => fee.refresh()}>
                Try again
              </Button>
            </CardBody>
          </Card>
        ) : markup === null ? (
          <Card className="mb-4" aria-busy="true">
            <CardBody className="pt-4 text-sm text-ink-muted">Loading the fee…</CardBody>
          </Card>
        ) : (
          <Card className="mb-4" aria-label="Fee in force">
            <CardHeader
              title="Fee in force"
              actions={
                <Badge tone={markup.source === 'setting' ? 'brand' : 'neutral'}>
                  {markup.source === 'setting' ? 'Set by an owner' : 'Boot default'}
                </Badge>
              }
            />
            <CardBody>
              <p className="text-3xl font-semibold text-ink" data-testid="fee-in-force">
                {markup.percent}%
              </p>
              <p className="mt-1 text-sm text-ink-muted">{describeSetBy(markup)}</p>
            </CardBody>
          </Card>
        )}

        {markup?.canSet === true ? (
          <Card className="mb-4">
            <CardHeader
              title="Change the fee"
              description="Applies to the next charge and every one after. Rows already recorded keep the fee they were charged at."
            />
            <CardBody>
              <form onSubmit={submit} noValidate aria-label="Change the platform fee">
                <Field
                  label="New fee"
                  hint="0 to 100, up to two decimals — the fee is kept in whole basis points."
                  error={fieldError}
                  className="max-w-xs"
                >
                  {({ id, describedBy, invalid }) => (
                    <Input
                      id={id}
                      aria-describedby={describedBy}
                      invalid={invalid}
                      inputMode="decimal"
                      autoComplete="off"
                      placeholder={markup.percent}
                      suffix="%"
                      value={draft}
                      onChange={(e) => {
                        setDraft(e.target.value);
                        setSaveError(null);
                      }}
                      onBlur={() => setTouched(draft.trim() !== '')}
                    />
                  )}
                </Field>
                {unchanged ? (
                  <p className="mt-2 text-xs text-ink-muted" data-testid="fee-unchanged">
                    That is the fee already in force.
                  </p>
                ) : null}
                {saveError !== null ? (
                  <p className="mt-2 text-sm text-fail" role="alert">
                    {saveError}
                  </p>
                ) : null}
                <div className="mt-3 flex items-center gap-2">
                  <Button type="submit" variant="primary" disabled={unchanged}>
                    Change fee…
                  </Button>
                </div>
              </form>
            </CardBody>
          </Card>
        ) : markup !== null ? (
          <Card className="mb-4">
            <CardBody className="pt-4">
              <p className="text-sm text-ink-muted" data-testid="fee-read-only">
                Only a platform owner can change the fee — an address on the deployment&apos;s owner
                list. Everyone signed in can read it, and every architect&apos;s usage card shows
                it.
              </p>
            </CardBody>
          </Card>
        ) : null}

        <Card aria-label="What architects see">
          <CardHeader
            title="What architects see"
            description="The usage card names the fee in force. The first metered job after a change — a generation, a render, a copilot call, an export — is the first charge debited at it."
          />
          <CardBody>
            {usage.usage === null ? (
              <p className="text-sm text-ink-muted">
                {usage.error !== null ? `Usage isn't available — ${usage.error}` : 'Loading usage…'}
              </p>
            ) : (
              <dl className="grid gap-1 text-sm">
                <div className="flex gap-2">
                  <dt className="text-ink-muted">Usage card says</dt>
                  <dd className="text-ink" data-testid="usage-fee">
                    {describeFee(usage.usage) ?? 'no platform fee'}
                  </dd>
                </div>
                {describeSpendBreakdown(usage.usage) !== null ? (
                  <div className="flex gap-2">
                    <dt className="text-ink-muted">Your own charges</dt>
                    <dd className="text-ink">{describeSpendBreakdown(usage.usage)}</dd>
                  </div>
                ) : null}
              </dl>
            )}
          </CardBody>
        </Card>
      </PageBody>

      <ConfirmDialog
        open={confirming && change !== null}
        onOpenChange={(open) => {
          if (!open) setConfirming(false);
        }}
        title={change?.title ?? ''}
        description={change?.description ?? ''}
        confirmLabel="Apply from the next charge"
        busy={saving}
        onConfirm={() => void confirm()}
      />
    </AppShell>
  );
}
