/**
 * SubmissionPanel.tsx — what still stands between this set and the counter (D-4).
 *
 * The compliance tab answers *is this design legal*. This panel answers a different
 * question that no compliance engine can: *is this SET submittable*. A fully compliant
 * design comes back across the counter unread when the khata number is missing from
 * the title block, and that rejection costs an architect a fortnight.
 *
 * Three decisions here are load-bearing:
 *
 * 1. **It never picks the authority.** Bengaluru has two — BBMP and BDA sanction
 *    different plots under the same `blr` rule pack — so when none is chosen the panel
 *    asks. Choosing the first would be a silent wrong answer that looks like a right
 *    one, and half of Bengaluru would work to the wrong checklist.
 * 2. **The tick never appears without its caveat.** Not one template has been checked
 *    against a published municipal checklist. `Ready` means the set contains what the
 *    template asks for, and the seed badge and the `verify` sentence sit beside it
 *    permanently — a green check this product cannot stand behind is worse than none.
 * 3. **Shortfalls are sentences, not counts.** "5 items outstanding" tells an
 *    architect nothing at 11pm; "BBMP wants KHATA NO. in the title block" tells them
 *    what to type.
 */

import { useCallback, useEffect, useState } from 'react';

import { Badge, Button, Field, Icon, Input, cn, useToast } from '@garh/ui';

import { AppError } from '../../lib/errors';
import {
  fetchProjectSubmission,
  fetchSubmissionReadiness,
  saveProjectSubmission,
  type ProjectSubmission,
  type SubmissionReadiness,
  type SubmissionTemplate,
} from './api';

export interface SubmissionPanelProps {
  projectId: string;
  className?: string;
}

export function SubmissionPanel({ projectId, className }: SubmissionPanelProps): JSX.Element {
  const { toast } = useToast();
  const [state, setState] = useState<ProjectSubmission | null>(null);
  const [readiness, setReadiness] = useState<SubmissionReadiness | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const next = await fetchProjectSubmission(projectId, signal);
        if (signal?.aborted) return;
        setState(next);
        setDraft(next.fields);
        const check = await fetchSubmissionReadiness(projectId, signal ? { signal } : {});
        if (!signal?.aborted) setReadiness(check);
      } catch (cause: unknown) {
        if (signal?.aborted) return;
        setError(
          cause instanceof AppError
            ? `${cause.message} ${cause.action}`
            : 'Could not load the submission checklist.',
        );
      }
    },
    [projectId],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const choose = useCallback(
    async (authority: string | null) => {
      setSaving(true);
      try {
        // Switching desks clears the old identifiers with it — see saveProjectSubmission.
        const next = await saveProjectSubmission(projectId, { authority, fields: {} });
        setState(next);
        setDraft(next.fields);
        setReadiness(await fetchSubmissionReadiness(projectId));
      } catch (cause: unknown) {
        toast({
          severity: 'fail',
          title: 'Could not set the authority',
          description: cause instanceof AppError ? cause.message : 'Try again.',
          action: { label: 'Got it', onClick: () => undefined },
        });
      } finally {
        setSaving(false);
      }
    },
    [projectId, toast],
  );

  const save = useCallback(async () => {
    if (!state?.authority) return;
    setSaving(true);
    try {
      const next = await saveProjectSubmission(projectId, {
        authority: state.authority,
        fields: draft,
      });
      setState(next);
      setReadiness(await fetchSubmissionReadiness(projectId));
      toast({ severity: 'pass', title: 'Submission details saved' });
    } catch (cause: unknown) {
      toast({
        severity: 'fail',
        title: 'Could not save',
        description: cause instanceof AppError ? cause.message : 'Try again.',
        action: { label: 'Got it', onClick: () => undefined },
      });
    } finally {
      setSaving(false);
    }
  }, [draft, projectId, state, toast]);

  if (error) {
    return (
      <section className={cn('rounded-lg border border-line p-4', className)}>
        <p className="text-sm text-fail">{error}</p>
      </section>
    );
  }
  if (!state)
    return <section className={cn('h-24 animate-pulse rounded-lg bg-line/40', className)} />;

  const template: SubmissionTemplate | undefined = state.available.find(
    (item) => item.authority === state.authority,
  );

  return (
    <section
      className={cn('rounded-lg border border-line p-4', className)}
      aria-label="Submission checklist"
      data-testid="submission-panel"
    >
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-medium text-ink">Submission checklist</h3>
        {template ? (
          <Badge tone="warn" title={template.citation}>
            {template.confidence} · {template.review}
          </Badge>
        ) : null}
      </header>

      {state.available.length === 0 ? (
        <p className="mt-2 text-sm text-ink-muted">
          Set this project&rsquo;s city rule pack to see the authorities that sanction there.
        </p>
      ) : null}

      {/* Never auto-selected. Bengaluru has two, and guessing is the failure mode. */}
      {state.available.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label="Sanctioning authority">
          {state.available.map((item) => (
            <Button
              key={item.authority}
              size="sm"
              variant={item.authority === state.authority ? 'primary' : 'secondary'}
              disabled={saving}
              title={item.title}
              onClick={() => void choose(item.authority)}
            >
              {item.shortTitle}
            </Button>
          ))}
        </div>
      ) : null}

      {template ? (
        <p className="mt-3 text-xs text-ink-muted">
          <Icon name="info" className="mr-1 inline h-3 w-3" aria-hidden />
          {template.verify}
        </p>
      ) : null}

      {/* The identifiers this authority wants printed in the title block. */}
      {template && template.statutoryFields.length > 0 ? (
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          {template.statutoryFields.map((field) => (
            <Field key={field.key} label={field.label} hint={field.note}>
              {/* Destructured, not spread: `describedBy` is not a DOM attribute, and
                  spreading it drops the hint's accessible association while leaking a
                  React warning. The rest of the app maps it by hand for that reason. */}
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  value={draft[field.key] ?? ''}
                  onChange={(event) =>
                    setDraft((current) => ({ ...current, [field.key]: event.currentTarget.value }))
                  }
                />
              )}
            </Field>
          ))}
        </div>
      ) : null}

      {template ? (
        <div className="mt-3">
          <Button size="sm" onClick={() => void save()} disabled={saving}>
            {saving ? 'Saving…' : 'Save submission details'}
          </Button>
        </div>
      ) : null}

      {readiness?.chooseFrom.length ? (
        <p className="mt-3 text-sm text-ink-muted">
          This plot is sanctioned by {readiness.chooseFrom.join(' or ').toUpperCase()} — pick one
          above. They want different sets, so we will not guess.
        </p>
      ) : null}

      {readiness?.authority ? (
        <div className="mt-4 border-t border-line pt-3" data-testid="submission-readiness">
          <p className="text-sm">
            <span className={cn('font-medium', readiness.ready ? 'text-pass' : 'text-ink')}>
              {readiness.ready ? 'Has everything the template asks for' : 'Not ready to submit'}
            </span>{' '}
            <span className="text-ink-muted">
              ({readiness.satisfied} of {readiness.total})
            </span>
          </p>
          {/* Stated every time the tick is, never as a dismissible one-off. */}
          {readiness.ready ? (
            <p className="mt-1 text-xs text-warn">
              This is a {readiness.confidence} checklist and is {readiness.review}. It does not mean
              the set will be sanctioned.
            </p>
          ) : null}
          {readiness.shortfalls.length > 0 ? (
            <ul className="mt-2 space-y-1 text-sm text-ink-muted">
              {readiness.shortfalls.map((shortfall) => (
                <li key={`${shortfall.kind}:${shortfall.what}`} className="flex gap-2">
                  <Icon
                    name="alert-triangle"
                    className="mt-0.5 h-3 w-3 shrink-0 text-warn"
                    aria-hidden
                  />
                  <span>{shortfall.detail}</span>
                </li>
              ))}
            </ul>
          ) : null}
          {readiness.advisories.map((note) => (
            <p key={note} className="mt-2 text-xs text-ink-muted">
              {note}
            </p>
          ))}
        </div>
      ) : null}
    </section>
  );
}

export default SubmissionPanel;
