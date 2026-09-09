/**
 * OverrideDialog — "Accept with reason" for one failing rule.
 *
 * Golden rule 5: compliance informs, never blocks; an architect overrides for
 * good reasons and the override is logged, not prevented. The dialog says
 * exactly what accepting does (the row stays red on the report and the
 * annexure; the solver stops treating it as a blocker; the reason is stamped
 * with who and when) so the button is a decision, not a dismissal.
 *
 * The reason is required — the server refuses fewer than three characters —
 * because an unexplained override is the one a municipal reviewer will query.
 */

import { useEffect, useState } from 'react';
import { Button, Dialog, Field, cn } from '@garh/ui';
import type { ComplianceIssueVM } from '../../components';

const REASON_MIN = 3;
const REASON_MAX = 500;

export interface OverrideDialogProps {
  issue: ComplianceIssueVM | null;
  onOpenChange: (open: boolean) => void;
  onSubmit: (ruleId: string, reason: string) => Promise<boolean>;
  busy?: boolean | undefined;
}

export function OverrideDialog({
  issue,
  onOpenChange,
  onSubmit,
  busy = false,
}: OverrideDialogProps): JSX.Element {
  const [reason, setReason] = useState('');
  const [touched, setTouched] = useState(false);

  // A fresh reason per rule: the last rule's text must not pre-fill the next.
  useEffect(() => {
    setReason('');
    setTouched(false);
  }, [issue?.ruleId]);

  const trimmed = reason.trim().replace(/\s+/g, ' ');
  const tooShort = trimmed.length < REASON_MIN;
  const error =
    touched && tooShort
      ? `Give a reason a reviewer could read — at least ${REASON_MIN} characters.`
      : undefined;

  async function submit(): Promise<void> {
    setTouched(true);
    if (issue === null || tooShort || busy) return;
    const ok = await onSubmit(issue.ruleId, trimmed);
    if (ok) onOpenChange(false);
  }

  return (
    <Dialog
      open={issue !== null}
      onOpenChange={onOpenChange}
      title="Accept this rule with a reason"
      description={
        issue === null ? undefined : (
          <>
            <span className="font-medium text-ink">{issue.message}</span>
            {issue.cite === undefined ? null : (
              <span className="mt-1 block text-ink-muted">
                {issue.cite} · {issue.ruleId}
              </span>
            )}
          </>
        )
      }
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={() => void submit()}
            loading={busy}
            loadingLabel="Recording…"
            disabled={issue === null}
          >
            Accept and log
          </Button>
        </>
      }
    >
      <form
        className="flex flex-col gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <Field
          label="Reason"
          required
          error={error}
          hint={`${trimmed.length}/${REASON_MAX}. Stamped with your name and the time; it prints on the compliance annexure.`}
        >
          {({ id, describedBy, invalid }) => (
            <textarea
              id={id}
              value={reason}
              rows={3}
              maxLength={REASON_MAX}
              autoFocus
              placeholder="e.g. Client-signed deviation letter attached; existing structure retained."
              onChange={(event) => setReason(event.target.value)}
              onBlur={() => setTouched(true)}
              aria-describedby={describedBy}
              aria-invalid={invalid ? true : undefined}
              className={cn(
                'garh-focus-ring min-h-[4.5rem] w-full resize-y rounded-md border bg-surface',
                'px-2.5 py-1.5 text-sm leading-5 text-ink placeholder:text-ink-subtle',
                invalid ? 'border-fail-line' : 'border-line-strong',
              )}
            />
          )}
        </Field>
        <ul className="list-disc space-y-1 pl-5 text-xs leading-5 text-ink-muted">
          <li>
            The rule keeps evaluating and stays marked as failing on the report and the annexure.
          </li>
          <li>Generate stops treating it as a blocker until the override is revoked.</li>
          <li>The decision is written to the project's audit log.</li>
        </ul>
      </form>
    </Dialog>
  );
}

export default OverrideDialog;
