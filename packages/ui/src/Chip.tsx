/**
 * Chip, ComplianceChip, AssumptionChip.
 *
 * Two golden rules live in this file.
 *
 * Golden rule 5 — "compliance never blocks, it informs". A ComplianceChip is
 * always non-modal: severity colour, ONE line of human text ("Bedroom 2 is
 * 8.9 m² — NBC needs 9.5 m²"), the citation on hover, and a "Fix it" button
 * when an auto-fix op is computable. It never prevents an action; the architect
 * can ignore every chip on the strip and still export.
 *
 * Golden rule 4 — "assumptions are visible". An AssumptionChip is an editable
 * value the AI filled in for you, with the reason it picked that value and a
 * citation where one exists. Editing commits through the normal op path, so an
 * assumption is not a special kind of state — it is a value with provenance.
 */

import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { cn } from './cn';
import { Icon } from './icons';
import type { IconName } from './icons';
import { Tooltip } from './Tooltip';

export type ChipSeverity = 'neutral' | 'info' | 'pass' | 'warn' | 'fail' | 'brand';
export type ChipSize = 'sm' | 'md';

const SKIN: Record<ChipSeverity, string> = {
  neutral: 'border-neutral-line bg-neutral-soft text-neutral-ink',
  info: 'border-info-line bg-info-soft text-info-ink',
  pass: 'border-pass-line bg-pass-soft text-pass-ink',
  warn: 'border-warn-line bg-warn-soft text-warn-ink',
  fail: 'border-fail-line bg-fail-soft text-fail-ink',
  brand: 'border-brand/30 bg-brand-soft text-brand-ink',
};

const SIZE: Record<ChipSize, string> = {
  sm: 'h-6 gap-1 px-2 text-2xs',
  md: 'h-7 gap-1.5 px-2.5 text-xs',
};

export const SEVERITY_ICON: Record<ChipSeverity, IconName | null> = {
  neutral: null,
  info: 'info',
  pass: 'check-circle',
  warn: 'alert-triangle',
  fail: 'alert-circle',
  brand: 'sparkles',
};

export interface ChipProps {
  children: ReactNode;
  severity?: ChipSeverity | undefined;
  size?: ChipSize | undefined;
  icon?: IconName | null | undefined;
  /** Makes the whole chip a button. */
  onClick?: (() => void) | undefined;
  /** Adds a trailing × . */
  onRemove?: (() => void) | undefined;
  removeLabel?: string | undefined;
  /** Renders the pressed state for filter chips. */
  selected?: boolean | undefined;
  title?: string | undefined;
  className?: string | undefined;
  /**
   * Accepted explicitly so a wrapping `<Tooltip>` can describe this chip. The
   * tooltip clones its child to set it, and a component that silently dropped
   * the prop would make the description unannounceable.
   */
  'aria-describedby'?: string | undefined;
}

export function Chip({
  children,
  severity = 'neutral',
  size = 'md',
  icon,
  onClick,
  onRemove,
  removeLabel = 'Remove',
  selected,
  title,
  className,
  'aria-describedby': ariaDescribedBy,
}: ChipProps): JSX.Element {
  const glyph = icon === undefined ? SEVERITY_ICON[severity] : icon;
  const body = (
    <>
      {glyph === null ? null : <Icon name={glyph} size={size === 'sm' ? 12 : 13} />}
      <span className="truncate">{children}</span>
    </>
  );

  const shell = cn(
    'inline-flex max-w-full items-center rounded-full border font-medium',
    SKIN[severity],
    SIZE[size],
    selected === true && 'ring-1 ring-inset ring-current',
    className,
  );

  if (onClick !== undefined) {
    return (
      <span className="inline-flex max-w-full items-center">
        <button
          type="button"
          title={title}
          aria-pressed={selected}
          aria-describedby={ariaDescribedBy}
          onClick={onClick}
          className={cn(shell, 'garh-focus-ring transition-shadow hover:shadow-sm')}
        >
          {body}
        </button>
        {onRemove === undefined ? null : <ChipRemove label={removeLabel} onRemove={onRemove} />}
      </span>
    );
  }

  return (
    <span className={shell} title={title} aria-describedby={ariaDescribedBy}>
      {body}
      {onRemove === undefined ? null : (
        <ChipRemove label={removeLabel} onRemove={onRemove} inline />
      )}
    </span>
  );
}

function ChipRemove({
  label,
  onRemove,
  inline = false,
}: {
  label: string;
  onRemove: () => void;
  inline?: boolean;
}): JSX.Element {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onRemove}
      className={cn(
        'garh-focus-ring rounded-full p-0.5 opacity-70 hover:opacity-100',
        inline ? '-mr-1 ml-0.5' : 'ml-1',
      )}
    >
      <Icon name="x" size={11} />
    </button>
  );
}

// ---------------------------------------------------------------------------
// ComplianceChip
// ---------------------------------------------------------------------------

/** Mirrors the rules-engine result status (`pass | warn | fail`) plus the
 *  "the rule did not apply here" case the engine also emits. */
export type ComplianceStatus = 'pass' | 'warn' | 'fail' | 'not_applicable';

const COMPLIANCE_SEVERITY: Record<ComplianceStatus, ChipSeverity> = {
  pass: 'pass',
  warn: 'warn',
  fail: 'fail',
  not_applicable: 'neutral',
};

export interface ComplianceChipProps {
  status: ComplianceStatus;
  /**
   * The one-line human sentence. Written by the rules layer, not here:
   * "Bedroom 2 is 8.9 m² — NBC needs 9.5 m²".
   */
  message: string;
  /** Citation shown on hover/focus: "NBC 2016 Part 3, Cl. 4.2" or "BBMP Table 6a". */
  cite?: string | undefined;
  /** Rule id, shown under the citation so a reviewer can grep the pack. */
  ruleId?: string | undefined;
  /**
   * Seeded rule values are marked `"confidence": "seed"` in the packs until a
   * local architect reviews them. We say so rather than implying certainty.
   */
  confidence?: 'seed' | 'reviewed' | 'verified' | undefined;
  /** Present only when an auto-fix op is computable (§15). */
  onFix?: (() => void) | undefined;
  fixLabel?: string | undefined;
  /** Selects/highlights the offending elements on the canvas. */
  onSelect?: (() => void) | undefined;
  size?: ChipSize | undefined;
  className?: string | undefined;
}

const CONFIDENCE_NOTE: Record<'seed' | 'reviewed' | 'verified', string> = {
  seed: 'Seed value — not yet reviewed by a local architect. Check against the current bye-law before submission.',
  reviewed: 'Reviewed by an empanelled local architect.',
  verified: 'Verified against the published bye-law text.',
};

export function ComplianceChip({
  status,
  message,
  cite,
  ruleId,
  confidence,
  onFix,
  fixLabel = 'Fix it',
  onSelect,
  size = 'md',
  className,
}: ComplianceChipProps): JSX.Element {
  const severity = COMPLIANCE_SEVERITY[status];
  const hasDetail = cite !== undefined || ruleId !== undefined || confidence !== undefined;

  const chip = (
    <span
      className={cn(
        'inline-flex max-w-full items-center rounded-full border font-medium',
        SKIN[severity],
        SIZE[size],
        className,
      )}
    >
      {SEVERITY_ICON[severity] === null ? null : (
        <Icon name={SEVERITY_ICON[severity]} size={size === 'sm' ? 12 : 13} />
      )}
      {onSelect === undefined ? (
        <span className="truncate">{message}</span>
      ) : (
        <button
          type="button"
          onClick={onSelect}
          className="garh-focus-ring truncate rounded-sm text-left hover:underline"
        >
          {message}
        </button>
      )}
      {onFix === undefined ? null : (
        <button
          type="button"
          onClick={onFix}
          className={cn(
            'garh-focus-ring -mr-1.5 ml-1 rounded-full border border-line bg-surface/70 px-2 py-0.5',
            'text-2xs font-semibold hover:bg-surface',
          )}
        >
          {fixLabel}
        </button>
      )}
      {/*
       * The citation, in the accessibility tree.
       *
       * The visual affordance for a citation is hover (§15), and a chip with no
       * `onSelect` and no `onFix` has nothing focusable in it — so a
       * hover-only tooltip would put the source of a compliance claim out of
       * reach of a screen-reader user entirely. Repeating it as visually-hidden
       * text costs nothing on screen and makes the claim and its provenance
       * arrive together however the chip is read.
       */}
      {hasDetail ? (
        <span className="sr-only">
          {cite === undefined ? '' : ` Source: ${cite}.`}
          {ruleId === undefined ? '' : ` Rule ${ruleId}.`}
          {confidence === undefined ? '' : ` ${CONFIDENCE_NOTE[confidence]}`}
        </span>
      ) : null}
    </span>
  );

  if (!hasDetail) return chip;

  return (
    <Tooltip
      delayMs={120}
      content={
        <span className="block space-y-1">
          {cite === undefined ? null : <span className="block font-medium text-ink">{cite}</span>}
          {ruleId === undefined ? null : (
            <span className="block font-mono text-2xs text-ink-subtle">{ruleId}</span>
          )}
          {confidence === undefined ? null : (
            <span className="block text-ink-muted">{CONFIDENCE_NOTE[confidence]}</span>
          )}
        </span>
      }
    >
      {chip}
    </Tooltip>
  );
}

// ---------------------------------------------------------------------------
// AssumptionChip
// ---------------------------------------------------------------------------

export interface AssumptionChipProps {
  /** What was assumed: "Floor height", "Budget", "Front setback". */
  label: string;
  /** The value, already formatted for display: "3,050 mm", "₹1,850 / sq ft". */
  valueText: string;
  /** Why the AI chose it. Always present — an assumption without a reason is a
   *  guess we are hiding. */
  reason: string;
  /** NBC clause / bye-law table, when the value came from a pack. */
  cite?: string | undefined;
  /**
   * Commit an edited value. The raw string is passed straight through: the
   * caller parses it (usually via `parseLengthMm`) because only the caller
   * knows whether this chip holds a length, a rate or a count.
   * Omit to render a read-only assumption.
   */
  onCommit?: ((raw: string) => void) | undefined;
  /** Set once the human has confirmed or overridden the value. */
  accepted?: boolean | undefined;
  className?: string | undefined;
}

/**
 * An editable chip with provenance. Click (or Enter/Space) turns the value into
 * a text field; Enter commits, Escape reverts. The chip keeps its "assumed"
 * styling until `accepted` is set, so at a glance you can see which numbers in
 * a plan are yours and which are ours.
 */
export function AssumptionChip({
  label,
  valueText,
  reason,
  cite,
  onCommit,
  accepted = false,
  className,
}: AssumptionChipProps): JSX.Element {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(valueText);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!editing) setDraft(valueText);
  }, [valueText, editing]);

  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  const commit = (): void => {
    setEditing(false);
    const next = draft.trim();
    if (next !== '' && next !== valueText) onCommit?.(next);
    else setDraft(valueText);
  };

  const skin = cn(
    'inline-flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs',
    accepted
      ? 'border-line bg-surface-muted text-ink-muted'
      : 'border-dashed border-brand/45 bg-brand-soft text-brand-ink',
    className,
  );

  if (editing) {
    return (
      <span className={skin}>
        <span className="text-2xs uppercase tracking-wide opacity-70">{label}</span>
        <input
          ref={inputRef}
          value={draft}
          aria-label={`${label} — edit assumed value`}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              commit();
            } else if (e.key === 'Escape') {
              e.preventDefault();
              setDraft(valueText);
              setEditing(false);
            }
          }}
          className="garh-focus-ring w-24 rounded-sm border-0 bg-surface px-1 py-0 text-xs font-semibold text-ink garh-nums"
        />
      </span>
    );
  }

  const content = (
    <span className={skin}>
      <span className="text-2xs uppercase tracking-wide opacity-70">{label}</span>
      {onCommit === undefined ? (
        <span className="font-semibold garh-nums">{valueText}</span>
      ) : (
        <button
          type="button"
          onClick={() => setEditing(true)}
          className="garh-focus-ring inline-flex items-center gap-1 rounded-sm font-semibold garh-nums hover:underline"
        >
          {valueText}
          <Icon name="edit" size={11} className="opacity-60" />
        </button>
      )}
      {/* Same reasoning as ComplianceChip: golden rule 4 says the assumption's
          reason is part of the value, so it cannot live only in a hover. */}
      <span className="sr-only">
        {accepted ? ' Your value.' : ' Assumed by Garh AI.'} {reason}
        {cite === undefined ? '' : ` Source: ${cite}.`}
      </span>
    </span>
  );

  return (
    <Tooltip
      delayMs={120}
      content={
        <span className="block space-y-1">
          <span className="block font-medium text-ink">
            {accepted ? 'Your value' : 'We assumed this'}
          </span>
          <span className="block text-ink-muted">{reason}</span>
          {cite === undefined ? null : (
            <span className="block text-2xs text-ink-subtle">Source: {cite}</span>
          )}
          {onCommit === undefined ? null : (
            <span className="block text-2xs text-ink-subtle">Click the value to change it.</span>
          )}
        </span>
      }
    >
      {content}
    </Tooltip>
  );
}
