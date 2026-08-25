/**
 * DiffPreview — ONE component, used by the copilot, the solver and compliance
 * auto-fixes (§12: "Diff preview component (used by copilot + solver): split
 * before/after mini-canvases + plain-language op list + apply/reject. One
 * component, both features.").
 *
 * Golden rule 3: "Every AI action is previewable and reversible." Nothing an
 * LLM or the solver produces reaches the model without passing through this
 * screen. Apply appends the ops as ONE group (so a single undo reverses the
 * whole thing); reject appends nothing at all — there is no partial state to
 * clean up.
 *
 * WHAT IS WIRED NOW vs LATER
 * The shell — header, op list, compliance deltas, apply/reject, keyboard
 * handling, loading and error states — is complete. The two mini-canvases are
 * `renderBefore` / `renderAfter` render props. Phase 4 owns the canvas, so it
 * supplies the real SVG/R3F thumbnails then; until it does, the slots render a
 * labelled placeholder rather than a fake plan. Because the contract is a prop,
 * wiring the real canvas later is a one-line change at each call site and
 * nothing in this file moves.
 */

import type { ReactNode } from 'react';
import { Badge, Button, Chip, ComplianceChip, Icon, Skeleton, cn } from '@garh/ui';
import type { IconName } from '@garh/ui';
import { ProblemPanel } from './ErrorBoundary';
import { complianceIssueKey } from './types';
import type { DiffOpKind, DiffPreviewVM, Problem } from './types';

const KIND_ICON: Readonly<Record<DiffOpKind, IconName>> = {
  add: 'plus',
  move: 'arrow-right',
  resize: 'ruler',
  remove: 'minus',
  edit: 'edit',
  assign: 'check',
};

const KIND_TONE: Readonly<Record<DiffOpKind, string>> = {
  add: 'bg-pass-soft text-pass-ink',
  move: 'bg-info-soft text-info-ink',
  resize: 'bg-info-soft text-info-ink',
  remove: 'bg-fail-soft text-fail-ink',
  edit: 'bg-neutral-soft text-neutral-ink',
  assign: 'bg-neutral-soft text-neutral-ink',
};

const SOURCE_HEADING: Readonly<Record<DiffPreviewVM['source'], string>> = {
  copilot: 'Here is what I would change',
  solver: 'Apply this option',
  autofix: 'Suggested fix',
};

const SOURCE_ICON: Readonly<Record<DiffPreviewVM['source'], IconName>> = {
  copilot: 'sparkles',
  solver: 'layers',
  autofix: 'shield-check',
};

export interface DiffPreviewProps {
  /** The proposal. `null` while it is being computed. */
  diff: DiffPreviewVM | null;
  /** True while the LLM/solver is still working. Shows skeletons, not a spinner. */
  loading?: boolean | undefined;
  /** A failure to produce a diff at all. Always carries a next action. */
  problem?: Problem | undefined;
  /**
   * The honest "I can't do that yet" path (§10). When the copilot returns
   * `cannotDo`, we show its sentence rather than approximating with wrong ops.
   */
  cannotDo?: string | undefined;
  /** The copilot's clarifying question, when it needs one before proposing ops. */
  needsClarification?: string | undefined;
  /**
   * Quick replies for the clarification card (Phase 6). Each chip answers the
   * question with one tap; free-text answers stay possible through whatever
   * input the caller owns. Ignored unless `needsClarification` is set.
   */
  clarificationChips?: readonly string[] | undefined;
  /** Called with the chosen quick reply. */
  onClarify?: ((reply: string) => void) | undefined;

  onApply: () => void;
  onReject: () => void;
  applying?: boolean | undefined;

  /** Retry the generation (also used as the ProblemPanel recovery). */
  onRetry?: (() => void) | undefined;

  /** Highlight the touched elements when an op row is hovered/clicked. */
  onHighlight?: ((elementIds: readonly string[]) => void) | undefined;

  /** Phase 4 supplies these. Until then a labelled placeholder is rendered. */
  renderBefore?: (() => ReactNode) | undefined;
  renderAfter?: (() => ReactNode) | undefined;

  className?: string | undefined;
}

export function DiffPreview({
  diff,
  loading = false,
  problem,
  cannotDo,
  needsClarification,
  clarificationChips,
  onClarify,
  onApply,
  onReject,
  applying = false,
  onRetry,
  onHighlight,
  renderBefore,
  renderAfter,
  className,
}: DiffPreviewProps): JSX.Element {
  if (problem !== undefined) {
    return (
      <div className={className}>
        <ProblemPanel problem={problem} onRetry={onRetry} />
      </div>
    );
  }

  if (cannotDo !== undefined) {
    return (
      <div
        className={cn(
          'flex flex-col gap-3 rounded-lg border border-line bg-surface p-4',
          className,
        )}
      >
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-surface-muted text-ink-muted">
          <Icon name="info" size={17} />
        </span>
        <div>
          <h3 className="text-sm font-semibold text-ink">Not something I can do yet</h3>
          <p className="mt-1 text-sm leading-6 text-ink-muted">{cannotDo}</p>
          <p className="mt-2 text-xs text-ink-subtle">
            We have logged the request. In the meantime the drawing tools on the left can do it by
            hand.
          </p>
        </div>
        <div>
          <Button variant="secondary" size="sm" onClick={onReject}>
            Close
          </Button>
        </div>
      </div>
    );
  }

  if (needsClarification !== undefined) {
    const chips = clarificationChips ?? [];
    return (
      <div className={cn('flex flex-col gap-3 rounded-lg border border-line bg-surface p-4', className)}>
        <h3 className="text-sm font-semibold text-ink">One quick question</h3>
        <p className="text-sm leading-6 text-ink-muted">{needsClarification}</p>
        {chips.length > 0 && onClarify !== undefined ? (
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Quick replies">
            {chips.map((chip) => (
              <Chip key={chip} onClick={() => onClarify(chip)}>
                {chip}
              </Chip>
            ))}
          </div>
        ) : null}
        <div>
          <Button variant="secondary" size="sm" onClick={onReject}>
            Never mind
          </Button>
        </div>
      </div>
    );
  }

  if (loading || diff === null) {
    return (
      <div
        className={cn('rounded-lg border border-line bg-surface p-4', className)}
        role="status"
        aria-live="polite"
        aria-busy="true"
      >
        <span className="sr-only">Working out what to change</span>
        <Skeleton className="h-4 w-48" />
        <div className="mt-3 grid grid-cols-2 gap-3">
          <Skeleton className="aspect-[4/3] w-full" shape="block" />
          <Skeleton className="aspect-[4/3] w-full" shape="block" />
        </div>
        <div className="mt-3 flex flex-col gap-2">
          <Skeleton className="h-3.5 w-full" />
          <Skeleton className="h-3.5 w-5/6" />
          <Skeleton className="h-3.5 w-2/3" />
        </div>
      </div>
    );
  }

  const opCount = diff.ops.length;

  return (
    <section
      aria-label="Proposed change"
      className={cn('flex flex-col overflow-hidden rounded-lg border border-line bg-surface', className)}
    >
      <header className="flex items-start gap-3 border-b border-line px-4 py-3">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-soft text-brand-ink">
          <Icon name={SOURCE_ICON[diff.source]} size={17} />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-ink">{SOURCE_HEADING[diff.source]}</h3>
          <p className="mt-0.5 text-sm leading-5 text-ink-muted">{diff.intent}</p>
        </div>
        <Badge tone="neutral">
          {opCount} change{opCount === 1 ? '' : 's'}
        </Badge>
      </header>

      {/* Before / after */}
      <div className="grid grid-cols-1 gap-3 border-b border-line p-4 sm:grid-cols-2">
        <MiniCanvas label="Now" render={renderBefore} />
        <MiniCanvas label="After this change" render={renderAfter} accent />
      </div>

      {/* Plain-language op list */}
      <div className="min-h-0 max-h-64 overflow-y-auto px-4 py-3">
        <h4 className="mb-2 text-2xs font-semibold uppercase tracking-wider text-ink-subtle">
          What changes
        </h4>
        <ul className="flex flex-col gap-1">
          {diff.ops.map((op) => (
            <li key={op.id}>
              <button
                type="button"
                disabled={onHighlight === undefined}
                onMouseEnter={() => onHighlight?.(op.elementIds)}
                onFocus={() => onHighlight?.(op.elementIds)}
                onClick={() => onHighlight?.(op.elementIds)}
                className={cn(
                  'garh-focus-ring flex w-full items-start gap-2 rounded-md px-1.5 py-1 text-left',
                  onHighlight === undefined ? 'cursor-default' : 'hover:bg-surface-muted',
                )}
              >
                <span
                  className={cn(
                    'mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded',
                    KIND_TONE[op.kind],
                  )}
                  aria-hidden="true"
                >
                  <Icon name={KIND_ICON[op.kind]} size={10} />
                </span>
                <span className="min-w-0 flex-1 text-xs leading-5 text-ink">{op.text}</span>
                <code
                  className="shrink-0 font-mono text-2xs text-ink-subtle"
                  title="The typed operation this becomes"
                >
                  {op.opType}
                </code>
              </button>
            </li>
          ))}
        </ul>

        {(diff.newIssues !== undefined && diff.newIssues.length > 0) ||
        (diff.resolvedIssues !== undefined && diff.resolvedIssues.length > 0) ? (
          <div className="mt-3 border-t border-line pt-3">
            <h4 className="mb-2 text-2xs font-semibold uppercase tracking-wider text-ink-subtle">
              Effect on the checks
            </h4>
            <div className="flex flex-wrap gap-1.5">
              {(diff.resolvedIssues ?? []).map((issue, index) => (
                <ComplianceChip
                  key={`fixed-${complianceIssueKey(issue)}#${index}`}
                  status="pass"
                  size="sm"
                  message={`Fixed: ${issue.message}`}
                  cite={issue.cite}
                  ruleId={issue.ruleId}
                  confidence={issue.confidence}
                />
              ))}
              {(diff.newIssues ?? []).map((issue, index) => (
                <ComplianceChip
                  key={`new-${complianceIssueKey(issue)}#${index}`}
                  status={issue.status}
                  size="sm"
                  message={`New: ${issue.message}`}
                  cite={issue.cite}
                  ruleId={issue.ruleId}
                  confidence={issue.confidence}
                />
              ))}
            </div>
          </div>
        ) : null}
      </div>

      <footer className="flex items-center justify-between gap-2 border-t border-line bg-surface-muted px-4 py-3">
        <p className="text-2xs text-ink-subtle">
          Applying counts as one step — a single undo puts everything back.
        </p>
        <div className="flex items-center gap-2">
          <Button variant="ghost" onClick={onReject} disabled={applying}>
            Reject
          </Button>
          <Button
            variant="primary"
            iconLeft="check"
            loading={applying}
            loadingLabel="Applying the change"
            onClick={onApply}
            disabled={opCount === 0}
          >
            Apply {opCount} change{opCount === 1 ? '' : 's'}
          </Button>
        </div>
      </footer>
    </section>
  );
}

function MiniCanvas({
  label,
  render,
  accent = false,
}: {
  label: string;
  render?: (() => ReactNode) | undefined;
  accent?: boolean;
}): JSX.Element {
  return (
    <figure className="m-0">
      <figcaption
        className={cn(
          'mb-1.5 text-2xs font-semibold uppercase tracking-wider',
          accent ? 'text-brand-ink' : 'text-ink-subtle',
        )}
      >
        {label}
      </figcaption>
      <div
        className={cn(
          'flex aspect-[4/3] w-full items-center justify-center overflow-hidden rounded-md border bg-surface-sunken',
          accent ? 'border-brand/40' : 'border-line',
        )}
      >
        {render === undefined ? (
          <span className="px-3 text-center text-2xs leading-4 text-ink-subtle">
            Plan thumbnails arrive with the 2D canvas in Phase&nbsp;4. The change list below is
            complete and accurate now.
          </span>
        ) : (
          render()
        )}
      </div>
    </figure>
  );
}
