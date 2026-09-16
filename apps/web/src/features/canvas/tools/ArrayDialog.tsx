/**
 * ArrayDialog.tsx — count and spacing, with the plan described before OK.
 *
 * The description ("Adds 12 walls and 6 openings") and any refusal ("give the
 * array a spacing…") come from `previewArray`, which runs the model's own
 * planner against the live document — the same call `runArray` will make on
 * OK — so the dialog cannot promise something the dispatch then refuses. One
 * undo afterwards, like every gesture in this feature.
 */

import { useMemo, useState, type JSX } from 'react';

import { Button, Dialog, LengthInput } from '@garh/ui';

import { describeSelection, type UnitsDisplay } from '@garh/model';

import { previewArray, runArray, type ArrayOptions } from './clipboard';

export interface ArrayDialogProps {
  readonly display: UnitsDisplay;
  readonly onClose: () => void;
}

const MAX_COUNT = 50;

function clampCount(value: number): number {
  if (!Number.isFinite(value)) return 1;
  return Math.min(MAX_COUNT, Math.max(1, Math.round(value)));
}

export function ArrayDialog({ display, onClose }: ArrayDialogProps): JSX.Element {
  const [options, setOptions] = useState<ArrayOptions>({
    countX: 3,
    countY: 1,
    spacingXMm: 3000,
    spacingYMm: 3000,
  });
  const [error, setError] = useState<string | null>(null);

  // Live: every keystroke re-plans, so the sentence below is always about
  // THESE numbers. The planner folds on a fork; on a four-wall selection that
  // is milliseconds, and the element cap keeps it there.
  const planned = useMemo(() => previewArray(options), [options]);

  const confirm = (): void => {
    const outcome = runArray(options);
    if (!outcome.ok) {
      setError(outcome.refusal.message);
      return;
    }
    onClose();
  };

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title="Array the selection"
      description="Repeat what is selected on a grid. Counts include the original; a negative spacing goes the other way."
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!planned.ok} onClick={confirm}>
            Array
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1 text-xs font-medium text-ink-muted">
            Across (count)
            <input
              type="number"
              min={1}
              max={MAX_COUNT}
              value={options.countX}
              className="garh-focus-ring rounded border border-line bg-surface px-2 py-1 text-sm text-ink garh-nums"
              onChange={(e) => {
                setOptions({ ...options, countX: clampCount(Number(e.target.value)) });
              }}
            />
          </label>
          <LengthInput
            label="Across (spacing)"
            valueMm={options.spacingXMm}
            onCommitMm={(mm) => {
              setOptions({ ...options, spacingXMm: mm });
            }}
            display={display}
            bareUnit="mm"
          />
          <label className="flex flex-col gap-1 text-xs font-medium text-ink-muted">
            Up (count)
            <input
              type="number"
              min={1}
              max={MAX_COUNT}
              value={options.countY}
              className="garh-focus-ring rounded border border-line bg-surface px-2 py-1 text-sm text-ink garh-nums"
              onChange={(e) => {
                setOptions({ ...options, countY: clampCount(Number(e.target.value)) });
              }}
            />
          </label>
          <LengthInput
            label="Up (spacing)"
            valueMm={options.spacingYMm}
            onCommitMm={(mm) => {
              setOptions({ ...options, spacingYMm: mm });
            }}
            display={display}
            bareUnit="mm"
          />
        </div>

        {planned.ok ? (
          <p className="text-xs leading-5 text-ink-muted" data-testid="array-summary">
            Adds {describeSelection(planned.plan.created)} — {String(planned.plan.instances)}{' '}
            {planned.plan.instances === 1 ? 'copy' : 'copies'} of{' '}
            {describeSelection(planned.plan.selected)}. One undo step.
          </p>
        ) : (
          <p className="text-xs leading-5 text-fail-ink" role="alert" data-testid="array-refusal">
            {planned.refusal.message}
          </p>
        )}
        {error === null ? null : (
          <p className="text-xs leading-5 text-fail-ink" role="alert">
            {error}
          </p>
        )}
      </div>
    </Dialog>
  );
}
