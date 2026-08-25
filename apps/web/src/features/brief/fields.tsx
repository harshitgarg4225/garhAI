/**
 * fields.tsx — form controls the brief needs that @garh/ui does not ship.
 *
 * INTERNAL to the brief feature (not exported from the barrel): a toggle row,
 * a count stepper, an area field (sq ft in, integer mm² out — golden rule 6
 * applied to areas), a rupee field with Indian grouping, and a compass
 * multi-toggle for Vastu zones. All of them are controlled, commit-on-intent
 * (blur/Enter for text, click for toggles) and keyboard-operable (§15
 * accessibility: full keyboard operability of panels/forms).
 */

import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';

import {
  DIRECTIONS_8,
  formatFixed,
  formatIndianNumber,
  parseAreaMm2,
  toSqft,
  type Direction8,
} from '@garh/model';
import { Field, Input, cn } from '@garh/ui';

import { parseRupees } from './types';

// ---------------------------------------------------------------------------
// ToggleField — a labelled switch
// ---------------------------------------------------------------------------

export interface ToggleFieldProps {
  readonly label: string;
  /** One quiet sentence under the label. */
  readonly hint?: string | undefined;
  /** Tri-state: undefined renders as off but unanswered (meter counts it so). */
  readonly value: boolean | undefined;
  readonly onChange: (value: boolean) => void;
  readonly disabled?: boolean | undefined;
  readonly className?: string | undefined;
}

/**
 * A native-checkbox-backed switch. The checkbox is the accessible control
 * (space toggles, label clicks, screen readers announce state); the pill is
 * presentation only.
 */
export function ToggleField({
  label,
  hint,
  value,
  onChange,
  disabled,
  className,
}: ToggleFieldProps): JSX.Element {
  const on = value === true;
  return (
    <label
      className={cn(
        'flex cursor-pointer items-start justify-between gap-3 py-1.5',
        disabled === true && 'cursor-not-allowed opacity-60',
        className,
      )}
    >
      <span className="min-w-0">
        <span className="block text-sm text-ink">{label}</span>
        {hint === undefined ? null : (
          <span className="mt-0.5 block text-2xs leading-4 text-ink-subtle">{hint}</span>
        )}
      </span>
      <span className="relative mt-0.5 inline-flex shrink-0">
        <input
          type="checkbox"
          className="peer sr-only"
          checked={on}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span
          aria-hidden="true"
          className={cn(
            'block h-5 w-9 rounded-full border transition-colors',
            'peer-focus-visible:ring-2 peer-focus-visible:ring-brand/60 peer-focus-visible:ring-offset-1',
            on ? 'border-brand bg-brand' : 'border-line-strong bg-surface-muted',
          )}
        />
        <span
          aria-hidden="true"
          className={cn(
            'absolute top-0.5 h-4 w-4 rounded-full bg-surface shadow-sm transition-all',
            on ? 'left-[18px]' : 'left-0.5',
          )}
        />
      </span>
    </label>
  );
}

// ---------------------------------------------------------------------------
// CountStepper — small integer counts (bedrooms, balconies, parking)
// ---------------------------------------------------------------------------

export interface CountStepperProps {
  readonly label: string;
  readonly labelHidden?: boolean | undefined;
  readonly value: number | undefined;
  readonly onChange: (value: number) => void;
  readonly min?: number | undefined;
  readonly max?: number | undefined;
  /** Shown while `value` is undefined — "Not set" reads more honestly than 0. */
  readonly emptyText?: string | undefined;
  readonly hint?: ReactNode | undefined;
  readonly className?: string | undefined;
}

export function CountStepper({
  label,
  labelHidden,
  value,
  onChange,
  min = 0,
  max = 20,
  emptyText = 'Not set',
  hint,
  className,
}: CountStepperProps): JSX.Element {
  const current = value ?? min;
  const step = (delta: number): void => {
    const next = Math.min(max, Math.max(min, (value ?? min) + delta));
    onChange(next);
  };
  return (
    <Field label={label} labelHidden={labelHidden} hint={hint} className={className}>
      {({ id, describedBy }) => (
        <span
          className="inline-flex h-9 items-stretch overflow-hidden rounded-md border border-line-strong bg-surface"
          role="group"
          aria-describedby={describedBy}
        >
          <button
            type="button"
            aria-label={`Fewer — ${label}`}
            disabled={value !== undefined && current <= min}
            onClick={() => step(-1)}
            className="garh-focus-ring w-8 border-r border-line text-ink-muted hover:bg-surface-muted disabled:opacity-40"
          >
            −
          </button>
          <output
            id={id}
            className={cn(
              'flex min-w-14 items-center justify-center px-2 text-sm font-medium garh-nums',
              value === undefined ? 'text-ink-subtle' : 'text-ink',
            )}
          >
            {value === undefined ? emptyText : formatIndianNumber(value)}
          </output>
          <button
            type="button"
            aria-label={`More — ${label}`}
            disabled={current >= max}
            onClick={() => step(1)}
            className="garh-focus-ring w-8 border-l border-line text-ink-muted hover:bg-surface-muted disabled:opacity-40"
          >
            +
          </button>
        </span>
      )}
    </Field>
  );
}

// ---------------------------------------------------------------------------
// AreaField — "target size OR AI decides"
// ---------------------------------------------------------------------------

export interface AreaFieldProps {
  readonly label: string;
  readonly labelHidden?: boolean | undefined;
  /** Integer mm², or null for "AI decides". */
  readonly valueMm2: number | null;
  readonly onCommit: (mm2: number | null) => void;
  readonly disabled?: boolean | undefined;
  readonly className?: string | undefined;
}

function areaText(mm2: number | null): string {
  return mm2 === null ? '' : formatFixed(toSqft(mm2), 0);
}

/**
 * Sq-ft text field where EMPTY means "AI decides" — the F2 per-room size
 * preference. Accepts "120", "120 sqft", "11 sqm", "13 gaj"; stores integer
 * mm² via `parseAreaMm2` (the areas twin of golden rule 6).
 */
export function AreaField({
  label,
  labelHidden,
  valueMm2,
  onCommit,
  disabled,
  className,
}: AreaFieldProps): JSX.Element {
  const [text, setText] = useState(() => areaText(valueMm2));
  const [error, setError] = useState<string | undefined>(undefined);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    if (editing) return;
    setText(areaText(valueMm2));
    setError(undefined);
  }, [valueMm2, editing]);

  const commit = (raw: string): void => {
    const trimmed = raw.trim();
    if (trimmed === '') {
      setError(undefined);
      setText('');
      if (valueMm2 !== null) onCommit(null); // cleared ⇒ back to "AI decides"
      return;
    }
    try {
      const mm2 = parseAreaMm2(trimmed, 'sqft');
      if (mm2 <= 0) {
        setError('This needs to be a positive area.');
        return;
      }
      setError(undefined);
      setText(areaText(mm2));
      if (mm2 !== valueMm2) onCommit(mm2);
    } catch {
      setError('We couldn’t read that. Try 120, 120 sq ft, 11 sqm or 13 gaj.');
    }
  };

  return (
    <Field
      label={label}
      labelHidden={labelHidden}
      hint={valueMm2 === null && !editing ? 'Empty = AI decides' : undefined}
      error={error}
      className={className}
    >
      {({ id, describedBy, invalid }) => (
        <Input
          id={id}
          type="text"
          inputMode="decimal"
          autoComplete="off"
          spellCheck={false}
          disabled={disabled}
          placeholder="AI decides"
          suffix="sq ft"
          value={text}
          aria-describedby={describedBy}
          invalid={invalid}
          className="garh-nums"
          onChange={(e) => {
            setText(e.target.value);
            if (error !== undefined) setError(undefined);
          }}
          onFocus={(e) => {
            setEditing(true);
            e.currentTarget.select();
          }}
          onBlur={(e) => {
            setEditing(false);
            commit(e.target.value);
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              commit(e.currentTarget.value);
              e.currentTarget.blur();
            } else if (e.key === 'Escape') {
              e.preventDefault();
              setText(areaText(valueMm2));
              setError(undefined);
              e.currentTarget.blur();
            }
          }}
        />
      )}
    </Field>
  );
}

// ---------------------------------------------------------------------------
// RupeeField — whole rupees, Indian grouping, L/Cr shorthand accepted
// ---------------------------------------------------------------------------

export interface RupeeFieldProps {
  readonly label: string;
  /** Whole rupees, or null for empty. */
  readonly value: number | null;
  readonly onCommit: (rupees: number) => void;
  readonly hint?: ReactNode | undefined;
  readonly placeholder?: string | undefined;
  readonly disabled?: boolean | undefined;
  readonly className?: string | undefined;
}

function rupeeText(value: number | null): string {
  return value === null ? '' : formatIndianNumber(value);
}

export function RupeeField({
  label,
  value,
  onCommit,
  hint,
  placeholder = '45,00,000',
  disabled,
  className,
}: RupeeFieldProps): JSX.Element {
  const [text, setText] = useState(() => rupeeText(value));
  const [error, setError] = useState<string | undefined>(undefined);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    if (editing) return;
    setText(rupeeText(value));
    setError(undefined);
  }, [value, editing]);

  const commit = (raw: string): void => {
    const trimmed = raw.trim();
    if (trimmed === '') {
      setText(rupeeText(value));
      setError(undefined);
      return;
    }
    const rupees = parseRupees(trimmed);
    if (rupees === null || rupees <= 0) {
      setError('We couldn’t read that. Try 45,00,000 — or shorthand like 45L or 1.2Cr.');
      return;
    }
    setError(undefined);
    setText(rupeeText(rupees));
    if (rupees !== value) onCommit(rupees);
  };

  return (
    <Field label={label} hint={hint} error={error} className={className}>
      {({ id, describedBy, invalid }) => (
        <Input
          id={id}
          type="text"
          inputMode="decimal"
          autoComplete="off"
          spellCheck={false}
          disabled={disabled}
          prefix="₹"
          placeholder={placeholder}
          value={text}
          aria-describedby={describedBy}
          invalid={invalid}
          className="garh-nums"
          onChange={(e) => {
            setText(e.target.value);
            if (error !== undefined) setError(undefined);
          }}
          onFocus={(e) => {
            setEditing(true);
            e.currentTarget.select();
          }}
          onBlur={(e) => {
            setEditing(false);
            commit(e.target.value);
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              commit(e.currentTarget.value);
              e.currentTarget.blur();
            } else if (e.key === 'Escape') {
              e.preventDefault();
              setText(rupeeText(value));
              setError(undefined);
              e.currentTarget.blur();
            }
          }}
        />
      )}
    </Field>
  );
}

// ---------------------------------------------------------------------------
// DirectionPicker — Vastu zone multi-toggle
// ---------------------------------------------------------------------------

export interface DirectionPickerProps {
  readonly label: string;
  readonly value: readonly Direction8[];
  readonly onChange: (zones: Direction8[]) => void;
  readonly disabled?: boolean | undefined;
  readonly className?: string | undefined;
}

/**
 * Eight compass toggles in a row. A toggle group, not a listbox: every zone
 * rule is a small SET of allowed directions, and seeing all eight with the
 * active ones lit is how an architect actually thinks about it.
 */
export function DirectionPicker({
  label,
  value,
  onChange,
  disabled,
  className,
}: DirectionPickerProps): JSX.Element {
  const toggle = (dir: Direction8): void => {
    const next = value.includes(dir)
      ? value.filter((d) => d !== dir)
      : [...DIRECTIONS_8.filter((d) => value.includes(d) || d === dir)];
    onChange([...next]);
  };
  return (
    <div
      role="group"
      aria-label={label}
      className={cn('flex flex-wrap gap-1', disabled === true && 'opacity-60', className)}
    >
      {DIRECTIONS_8.map((dir) => {
        const active = value.includes(dir);
        return (
          <button
            key={dir}
            type="button"
            disabled={disabled}
            aria-pressed={active}
            onClick={() => toggle(dir)}
            className={cn(
              'garh-focus-ring h-7 min-w-9 rounded-md border px-1.5 text-2xs font-semibold transition-colors',
              active
                ? 'border-brand bg-brand-soft text-brand-ink'
                : 'border-line bg-surface text-ink-subtle hover:border-ink-subtle hover:text-ink-muted',
            )}
          >
            {dir}
          </button>
        );
      })}
    </div>
  );
}
