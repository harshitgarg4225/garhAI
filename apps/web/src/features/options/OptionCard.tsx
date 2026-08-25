/**
 * OptionCard — one presentable plan (§15 options screen): mini-plan SVG,
 * composite score ring, compliance badge, Vastu wheel, the three key stats,
 * and the "Why this plan" rationale expander whose assumption chips become
 * editable once the option is applied (edits dispatch ops — the locked golden
 * rule 4, never dead text).
 *
 * Purely presentational: every action arrives as a prop from OptionsPanel,
 * which owns the store wiring. That keeps this file renderable in isolation
 * and the actions testable without a DOM.
 */

import { useState } from 'react';

import {
  AssumptionChip,
  Button,
  Chip,
  Icon,
  ProgressRing,
  Tooltip,
  cn,
} from '@garh/ui';

import { MiniPlanSvg } from './MiniPlanSvg';
import { VastuWheel } from './VastuWheel';
import { miniPlanFromOption } from './planGeometry';
import {
  assumptionLabel,
  assumptionValueText,
  complianceSummary,
  keyStats,
  vastuWheel,
} from './stats';
import type { PlanOption, PtMm } from './types';

export interface OptionCardProps {
  readonly option: PlanOption;
  /** Index in the outcome's options array — what `solver.apply_option` needs. */
  readonly optionIndex: number;
  /** Plot or envelope outline, drawn faintly under the plan. */
  readonly outline?: readonly PtMm[] | undefined;
  /** True when this option is the one currently applied to the model. */
  readonly applied?: boolean | undefined;
  readonly onApply: () => void;
  readonly onMoreLikeThis?: (() => void) | undefined;
  /** Compare-two selection. */
  readonly compareSelected?: boolean | undefined;
  readonly onToggleCompare?: (() => void) | undefined;
  /**
   * Commit an edited assumption chip. Only offered once the option is applied
   * (before that there is nothing in the model for the op to patch); the
   * handler dispatches the op and returns false when the edit did not parse.
   */
  readonly onEditAssumption?: ((field: string, raw: string) => boolean) | undefined;
  readonly className?: string | undefined;
}

export function OptionCard({
  option,
  optionIndex,
  outline,
  applied = false,
  onApply,
  onMoreLikeThis,
  compareSelected = false,
  onToggleCompare,
  onEditAssumption,
  className,
}: OptionCardProps): JSX.Element {
  const [storeyIndex, setStoreyIndex] = useState<number | null>(null);
  const [expanded, setExpanded] = useState(false);

  const geometry = miniPlanFromOption(option);
  const stats = keyStats(option);
  const badge = complianceSummary(option.compliance);
  const wheel = vastuWheel(option);

  const floors = geometry.storeyIndices;
  const activeFloor = storeyIndex ?? floors[0] ?? 0;

  return (
    <article
      aria-label={`Plan option ${optionIndex + 1}`}
      className={cn(
        'flex flex-col gap-3 rounded-lg border bg-surface p-4',
        compareSelected ? 'border-brand' : 'border-line',
        className,
      )}
    >
      <header className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">
          Option {optionIndex + 1}
          {applied ? (
            <span className="ml-2 align-middle">
              <Chip severity="pass" size="sm" icon="check">
                Applied
              </Chip>
            </span>
          ) : null}
        </h3>
        {onToggleCompare !== undefined ? (
          <label className="flex cursor-pointer items-center gap-1.5 text-xs text-ink-muted">
            <input
              type="checkbox"
              checked={compareSelected}
              onChange={onToggleCompare}
              className="h-3.5 w-3.5"
            />
            Compare
          </label>
        ) : null}
      </header>

      <MiniPlanSvg
        geometry={geometry}
        storeyIndex={activeFloor}
        outline={outline}
        label={`Option ${optionIndex + 1} floor plan`}
      />

      {floors.length > 1 ? (
        <div role="tablist" aria-label="Floor" className="flex gap-1">
          {floors.map((floor) => (
            <button
              key={floor}
              role="tab"
              aria-selected={floor === activeFloor}
              onClick={() => setStoreyIndex(floor)}
              className={cn(
                'rounded px-2 py-0.5 text-2xs',
                floor === activeFloor
                  ? 'bg-surface-sunken font-medium text-ink'
                  : 'text-ink-subtle hover:text-ink',
              )}
            >
              {floorName(floor)}
            </button>
          ))}
        </div>
      ) : null}

      <div className="flex items-center gap-4">
        <ProgressRing value={option.scores.composite} label="Composite score" caption="score" />

        <div className="min-w-0 flex-1 space-y-1 text-xs text-ink-muted">
          <p className="garh-nums font-medium text-ink">{stats.builtUpLabel}</p>
          <p>{stats.bedroomsLabel}</p>
          <p className="garh-nums">{stats.circulationLabel}</p>
        </div>

        <VastuWheel wheel={wheel} size={72} />
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <Tooltip content="Rule checks this plan passed — every hard rule passes before a plan is shown.">
          <Chip severity="pass" size="sm" icon="shield">
            {badge.pass} pass
          </Chip>
        </Tooltip>
        {badge.warn > 0 ? (
          <Chip severity="warn" size="sm">
            {badge.warn} to review
          </Chip>
        ) : null}
        {badge.fail > 0 ? (
          // Soft fails only — §5.6 discards hard-fail plans before this screen.
          <Chip severity="fail" size="sm">
            {badge.fail} advisory {badge.fail === 1 ? 'flag' : 'flags'}
          </Chip>
        ) : null}
      </div>

      <div>
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-1 text-xs font-medium text-ink-muted hover:text-ink"
        >
          <Icon name="lightbulb" size={14} />
          Why this plan
          <Icon name={expanded ? 'minus' : 'plus'} size={12} />
        </button>

        {expanded ? (
          <div className="mt-2 space-y-2">
            {option.rationaleFacts.length > 0 ? (
              <ul className="flex flex-wrap gap-1.5">
                {option.rationaleFacts.map((fact, i) => (
                  <li key={i}>
                    <Chip size="sm" severity="neutral">
                      {fact}
                    </Chip>
                  </li>
                ))}
              </ul>
            ) : null}

            {option.assumptions.length > 0 ? (
              <div>
                <p className="mb-1 text-2xs font-medium uppercase tracking-wide text-ink-subtle">
                  Assumptions the AI made
                </p>
                <ul className="flex flex-wrap gap-1.5">
                  {option.assumptions.map((assumption) => (
                    <li key={assumption.field}>
                      <AssumptionChip
                        label={assumptionLabel(assumption.field)}
                        valueText={assumptionValueText(assumption.field, assumption.value)}
                        reason={assumption.reason}
                        cite={assumption.cite ?? undefined}
                        onCommit={
                          applied && onEditAssumption !== undefined
                            ? (raw) => {
                                onEditAssumption(assumption.field, raw);
                              }
                            : undefined
                        }
                      />
                    </li>
                  ))}
                </ul>
                {!applied ? (
                  <p className="mt-1 text-2xs text-ink-subtle">
                    Apply this option to edit its assumptions.
                  </p>
                ) : null}
              </div>
            ) : null}

            {option.rationaleFacts.length === 0 && option.assumptions.length === 0 ? (
              <p className="text-xs text-ink-subtle">
                No extra notes for this plan — the scores above are the whole story.
              </p>
            ) : null}
          </div>
        ) : null}
      </div>

      <footer className="mt-auto flex items-center gap-2 pt-1">
        <Button variant="primary" size="sm" onClick={onApply} disabled={applied}>
          {applied ? 'Applied' : 'Use this plan'}
        </Button>
        {onMoreLikeThis !== undefined ? (
          <Tooltip content="Generate new options in the same family as this plan.">
            <Button variant="ghost" size="sm" onClick={onMoreLikeThis}>
              <Icon name="sparkles" size={14} /> More like this
            </Button>
          </Tooltip>
        ) : null}
      </footer>
    </article>
  );
}

export function floorName(index: number): string {
  if (index === 0) return 'Ground';
  if (index === 1) return 'First';
  if (index === 2) return 'Second';
  if (index === 3) return 'Third';
  return `Floor ${index}`;
}
