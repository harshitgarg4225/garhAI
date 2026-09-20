/**
 * PrivacySection — `/settings/privacy`: the firm's audit trail (F-5) and the
 * DPDP rights (F-6) that were API-complete with nothing calling them.
 *
 * Three parts, in the order they matter to the person reading:
 *
 *   1. **Who did what** — the trail, newest first, grouped by day, filtered by
 *      action (the vocabulary comes from the server, never a hard-coded copy) and
 *      by date. Admin-only: a member gets the server's 403 rendered as a refusal
 *      that says so, not an empty table.
 *   2. **Download everything we hold on you** (DPDP §11) — the export saved as a
 *      JSON file. The request itself is an audited event, and the card says so.
 *   3. **Delete your account** (DPDP §12) — irreversible, so it costs a sentence:
 *      the address typed back, and a confirm that states plainly what survives.
 *      The op log survives with the actor cleared, and the copy explains why in
 *      the product's own words rather than hiding it behind "some data".
 */

import { useState, type FormEvent, type JSX } from 'react';

import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  ConfirmDialog,
  Field,
  Input,
  SelectField,
  useToast,
} from '@garh/ui';

import { ProblemPanel, toProblem } from '../../components';
import { api, type ApiClient, type Erasure } from '../../lib/api';
import { AppError } from '../../lib/errors';
import { selectIsAdmin, useSessionStore } from '../../stores/session';
import {
  describeAction,
  describeActor,
  describeEntity,
  formatWhen,
  groupByDay,
  metaPairs,
} from './audit';
import { useAuditTrail } from './useAuditTrail';

export interface PrivacySectionProps {
  /** Injected by tests; the app uses the singleton. */
  readonly client?: ApiClient | undefined;
  /** After erasure the account is gone; the router sends them to /login. */
  readonly onErased?: (() => void) | undefined;
}

/** Hands the export to the browser as a file. Revoked immediately after. */
function saveJson(filename: string, document_: unknown): void {
  const blob = new Blob([JSON.stringify(document_, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function PrivacySection({ client = api, onErased }: PrivacySectionProps): JSX.Element {
  const user = useSessionStore((s) => s.user);
  const isAdmin = useSessionStore(selectIsAdmin);
  const signOut = useSessionStore((s) => s.signOut);
  const { toast } = useToast();

  const [action, setAction] = useState('');
  const [since, setSince] = useState('');
  const trail = useAuditTrail({ action, since }, client, isAdmin);

  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | undefined>(undefined);

  const [confirmEmail, setConfirmEmail] = useState('');
  const [erasing, setErasing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [eraseError, setEraseError] = useState<string | undefined>(undefined);
  const [erased, setErased] = useState<Erasure | null>(null);

  const emailMatches =
    user !== null && confirmEmail.trim().toLowerCase() === user.email.trim().toLowerCase();

  async function downloadExport(): Promise<void> {
    setExporting(true);
    setExportError(undefined);
    try {
      const document_ = await client.privacy.export();
      const stamp = new Date().toISOString().slice(0, 10);
      saveJson(`garh-my-data-${stamp}.json`, document_);
      toast({
        severity: 'pass',
        title: 'Your data has been downloaded',
        description: 'The download itself is recorded in the audit trail.',
      });
    } catch (err) {
      setExportError(toProblem(err).message);
    } finally {
      setExporting(false);
    }
  }

  function askToErase(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    setEraseError(undefined);
    if (!emailMatches) {
      setEraseError('Type your own sign-in address, exactly, to confirm.');
      return;
    }
    setConfirming(true);
  }

  async function erase(): Promise<void> {
    setErasing(true);
    try {
      const outcome = await client.privacy.erase({ confirmEmail: confirmEmail.trim() });
      setErased(outcome);
      setConfirming(false);
      // The account is gone; the session with it. Clear locally, then leave.
      await signOut().catch(() => undefined);
      onErased?.();
    } catch (err) {
      const problem = AppError.from(err);
      setConfirming(false);
      setEraseError(`${problem.message} ${problem.action}`.trim());
    } finally {
      setErasing(false);
    }
  }

  const days = groupByDay(trail.entries);

  return (
    <div className="flex flex-col gap-6">
      {/* ── F-5: the trail ─────────────────────────────────────────────── */}
      <Card aria-label="Audit trail">
        <CardHeader
          title="Audit trail"
          description="Who did what in this practice, newest first. Kept as the integrity record for drawings you submit."
          actions={
            <Button size="sm" iconLeft="refresh" onClick={trail.refresh} disabled={!isAdmin}>
              Refresh
            </Button>
          }
        />
        <CardBody>
          {!isAdmin ? (
            <p className="text-sm text-ink-muted" data-testid="audit-admin-only">
              The trail says which colleague did what and from which address, so only an admin of
              this practice can read it. Ask an admin if you need a record of something.
            </p>
          ) : (
            <>
              <div className="mb-4 flex flex-wrap items-end gap-3">
                <SelectField
                  label="Action"
                  value={action}
                  fieldClassName="min-w-[16rem]"
                  onValueChange={(next) => setAction(next)}
                  options={[
                    { value: '', label: 'Everything' },
                    ...trail.actions.map((value) => ({
                      value,
                      label: `${value} — ${describeAction(value)}`,
                    })),
                  ]}
                />
                <Field label="Since" hint="Rows from this date onwards.">
                  {({ id, describedBy }) => (
                    <Input
                      id={id}
                      type="date"
                      value={since}
                      aria-describedby={describedBy}
                      onChange={(e) => setSince(e.target.value)}
                    />
                  )}
                </Field>
                {action === '' && since === '' ? null : (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setAction('');
                      setSince('');
                    }}
                  >
                    Clear filters
                  </Button>
                )}
              </div>

              {trail.error !== null ? (
                <ProblemPanel problem={toProblem(trail.error)} onRetry={trail.refresh} />
              ) : trail.loading ? (
                <p className="text-sm text-ink-muted" aria-busy="true">
                  Reading the trail…
                </p>
              ) : trail.entries.length === 0 ? (
                <p className="text-sm text-ink-muted" data-testid="audit-empty">
                  Nothing recorded{action === '' && since === '' ? ' yet' : ' for that filter'}.
                </p>
              ) : (
                <>
                  <ul className="flex flex-col gap-5" data-testid="audit-list">
                    {days.map((group) => (
                      <li key={group.day}>
                        <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-muted">
                          {group.day}
                        </h3>
                        <ul className="divide-y divide-line">
                          {group.items.map((entry) => {
                            const pairs = metaPairs(entry.meta);
                            return (
                              <li key={entry.id} className="py-2.5" data-testid="audit-row">
                                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm">
                                  <span className="font-medium text-ink">
                                    {describeActor(entry)}
                                  </span>
                                  <span className="text-ink-muted">
                                    {describeAction(entry.action)}
                                  </span>
                                  <Badge tone="neutral">{describeEntity(entry)}</Badge>
                                  <span className="ml-auto text-xs tabular-nums text-ink-subtle">
                                    {formatWhen(entry.at)}
                                  </span>
                                </div>
                                {pairs.length === 0 ? null : (
                                  <dl className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-ink-subtle">
                                    {/* <div>, not <span>: a <dl> may only directly
                                        contain dt/dd, script, template or div — axe
                                        (definition-list, dlitem) is right to refuse
                                        the span, and a screen reader loses the
                                        term/description pairing with it. */}
                                    {pairs.map((pair) => (
                                      <div key={pair.key} className="flex gap-1">
                                        <dt className="font-medium">{pair.key}</dt>
                                        <dd className="font-mono">{pair.value}</dd>
                                      </div>
                                    ))}
                                  </dl>
                                )}
                              </li>
                            );
                          })}
                        </ul>
                      </li>
                    ))}
                  </ul>
                  {trail.hasMore ? (
                    <div className="mt-4 flex justify-center">
                      <Button
                        size="sm"
                        loading={trail.loadingMore}
                        loadingLabel="Loading"
                        onClick={trail.loadMore}
                        data-testid="audit-more"
                      >
                        Load older entries
                      </Button>
                    </div>
                  ) : null}
                </>
              )}
            </>
          )}
        </CardBody>
      </Card>

      {/* ── F-6: DPDP §11 ──────────────────────────────────────────────── */}
      <Card aria-label="Your data">
        <CardHeader
          title="Download your data"
          description="Everything this product holds about you personally, as one JSON file (DPDP Act 2023, §11)."
        />
        <CardBody>
          <p className="mb-3 text-sm text-ink-muted">
            Your profile, the devices signed in as you, your second-factor state, your sign-in
            history, your comments, and the extent of your design authorship — how many edits, on
            which projects, when. The edits themselves are the practice&rsquo;s design data and are
            not in the file; what is recorded about you is that you made them. Downloading this is
            itself recorded in the trail above.
          </p>
          {exportError === undefined ? null : (
            <p className="mb-2 text-sm text-fail" role="alert">
              {exportError}
            </p>
          )}
          <Button
            iconLeft="download"
            loading={exporting}
            loadingLabel="Preparing your data"
            onClick={() => void downloadExport()}
            data-testid="privacy-export"
          >
            Download my data
          </Button>
        </CardBody>
      </Card>

      {/* ── F-6: DPDP §12 ──────────────────────────────────────────────── */}
      <Card className="border-fail" aria-label="Delete your account">
        <CardHeader
          title="Delete your account"
          description="Irreversible, and it takes effect immediately (DPDP Act 2023, §12)."
        />
        <CardBody>
          {erased !== null ? (
            <p className="text-sm text-ink" role="status" data-testid="privacy-erased">
              Your account has been deleted. {erased.opsAnonymised} design edits and{' '}
              {erased.commentsAnonymised} comments were kept with your name removed,{' '}
              {erased.sessionsEnded} sessions were ended, and {erased.auditEntriesRetained} audit
              rows are retained as the integrity record.
            </p>
          ) : (
            <>
              <p className="mb-3 text-sm text-ink-muted">
                Your name, email and CoA number are deleted. What stays, and why: your design edits
                stay with your name removed — an edit is a sentence of the drawing, not a record
                about you, and deleting them would corrupt drawings your colleagues and municipal
                offices rely on. Audit rows stay too; they hold an account id and an address, never
                your name, and they are the integrity record for a regulated deliverable.
              </p>
              <form
                className="flex flex-col gap-3"
                onSubmit={askToErase}
                data-testid="erasure-form"
              >
                <Field
                  label="Type your email address to confirm"
                  error={eraseError}
                  hint={user === null ? undefined : `The address you signed in with: ${user.email}`}
                >
                  {({ id, describedBy, invalid }) => (
                    <Input
                      id={id}
                      value={confirmEmail}
                      autoComplete="off"
                      aria-describedby={describedBy}
                      invalid={invalid}
                      onChange={(e) => {
                        setConfirmEmail(e.target.value);
                        if (eraseError !== undefined) setEraseError(undefined);
                      }}
                    />
                  )}
                </Field>
                <div>
                  <Button
                    type="submit"
                    variant="danger"
                    disabled={!emailMatches}
                    data-testid="erasure-submit"
                  >
                    Delete my account
                  </Button>
                </div>
              </form>
            </>
          )}
        </CardBody>
      </Card>

      <ConfirmDialog
        open={confirming}
        onOpenChange={(open) => setConfirming(open)}
        title="Delete your account?"
        confirmLabel="Delete my account"
        destructive
        busy={erasing}
        onConfirm={() => void erase()}
        description={
          <span className="text-sm text-ink-muted">
            This cannot be undone. You will be signed out of every device immediately. Your design
            edits and comments stay in the practice&rsquo;s projects with your name removed, and the
            audit rows that name your account id are retained as the integrity record.
          </span>
        }
      />
    </div>
  );
}

export default PrivacySection;
