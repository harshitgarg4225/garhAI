/**
 * Input, LengthInput, PhoneInput.
 *
 * LengthInput is the load-bearing one. Golden rule 6 is "mm in, pretty out":
 * the model stores integer millimetres and NOTHING else, so every place a human
 * types a length it must pass through `parseLengthMm` from @garh/model, and
 * every place one is shown it must pass through `formatLength`. This component
 * is that boundary, and it is the only one in the app allowed to hold a length
 * as a string.
 *
 * Behaviour worth knowing:
 *  - The field is text, not number. `<input type="number">` cannot hold `12'6"`
 *    and its spinners quietly mangle the value on scroll.
 *  - Commit happens on blur and on Enter. Escape reverts to the last committed
 *    value. While you are typing, nothing is dispatched — a half-typed "12'"
 *    is not a wall move.
 *  - On a successful parse the text is REWRITTEN to the canonical formatting
 *    for the project's units, so the user sees exactly what was stored. The
 *    exact millimetre value is always shown as the hint, because that is the
 *    number that ends up on the municipal drawing.
 *  - A parse failure is never destructive: the old value stays in the model and
 *    the field explains the formats that do work.
 */

import { forwardRef, useEffect, useState } from 'react';
import type { InputHTMLAttributes, ReactNode, TextareaHTMLAttributes } from 'react';
import {
  formatIndianNumber,
  formatLength,
  tryParseLengthMm,
  type UnitsDisplay,
} from '@garh/model';
import { cn } from './cn';
import { CONTROL_CLASS, CONTROL_INVALID_CLASS, Field } from './Field';
import { Icon } from './icons';
import type { IconName } from './icons';

// ---------------------------------------------------------------------------
// Input — plain text/email/etc.
// ---------------------------------------------------------------------------

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'className'> {
  invalid?: boolean | undefined;
  iconLeft?: IconName | undefined;
  /** Static text pinned inside the right edge, e.g. "mm" or "sq ft". */
  suffix?: ReactNode | undefined;
  /** Static text pinned inside the left edge, e.g. "+91". */
  prefix?: ReactNode | undefined;
  className?: string | undefined;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { invalid = false, iconLeft, suffix, prefix, className, ...rest },
  ref,
) {
  return (
    <div className="relative flex items-center">
      {iconLeft !== undefined ? (
        <Icon name={iconLeft} size={15} className="pointer-events-none absolute left-2.5 text-ink-subtle" />
      ) : null}
      {prefix !== undefined ? (
        <span className="pointer-events-none absolute left-2.5 text-sm text-ink-muted garh-nums">
          {prefix}
        </span>
      ) : null}
      <input
        ref={ref}
        aria-invalid={invalid || undefined}
        className={cn(
          CONTROL_CLASS,
          invalid && CONTROL_INVALID_CLASS,
          iconLeft !== undefined && 'pl-8',
          prefix !== undefined && 'pl-11',
          suffix !== undefined && 'pr-12',
          className,
        )}
        {...rest}
      />
      {suffix !== undefined ? (
        <span className="pointer-events-none absolute right-2.5 text-xs text-ink-subtle">{suffix}</span>
      ) : null}
    </div>
  );
});

// ---------------------------------------------------------------------------
// Textarea
// ---------------------------------------------------------------------------

export interface TextareaProps
  extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'className'> {
  invalid?: boolean | undefined;
  className?: string | undefined;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { invalid = false, className, rows = 4, ...rest },
  ref,
) {
  return (
    <textarea
      ref={ref}
      rows={rows}
      aria-invalid={invalid || undefined}
      className={cn(
        CONTROL_CLASS,
        'h-auto resize-y py-2 leading-relaxed',
        invalid && CONTROL_INVALID_CLASS,
        className,
      )}
      {...rest}
    />
  );
});

// ---------------------------------------------------------------------------
// LengthInput — THE mm boundary
// ---------------------------------------------------------------------------

/** Formats that always work, shown when a parse fails. Kept short on purpose. */
const LENGTH_EXAMPLES: Record<UnitsDisplay, string> = {
  'ft-in': `12'6", 12-6, 3.8m or 3810`,
  m: `3.8m, 3810, or 12'6"`,
};

export interface LengthInputProps {
  /** Current value in INTEGER millimetres. `null` renders an empty field. */
  valueMm: number | null;
  /** Called only with a successfully parsed integer-mm value. */
  onCommitMm: (mm: number) => void;
  /** Project display units. Drives formatting and the bare-number meaning. */
  display?: UnitsDisplay | undefined;
  /**
   * What a bare number means. Defaults to the display units, which is what an
   * architect expects in a ft-in project ("12" in a wall-length box is 12 feet).
   * Pass `'mm'` for fields that are natively millimetre-ish (wall thickness).
   */
  bareUnit?: 'mm' | 'ft-in' | 'm' | undefined;
  label: string;
  labelHidden?: boolean | undefined;
  hint?: ReactNode | undefined;
  required?: boolean | undefined;
  disabled?: boolean | undefined;
  placeholder?: string | undefined;
  /** Inclusive bounds in mm. Violations become a friendly error, not a clamp. */
  minMm?: number | undefined;
  maxMm?: number | undefined;
  /** Rejects negatives with a readable message. Default true. */
  nonNegative?: boolean | undefined;
  /** Hides the "= 3,810 mm" helper. Only do this where space is desperate. */
  hideMmHint?: boolean | undefined;
  className?: string | undefined;
  name?: string | undefined;
  autoFocus?: boolean | undefined;
}

function displayText(mm: number | null, display: UnitsDisplay): string {
  if (mm === null) return '';
  return formatLength(mm, display, { dropZeroInches: false });
}

export function LengthInput({
  valueMm,
  onCommitMm,
  display = 'ft-in',
  bareUnit,
  label,
  labelHidden,
  hint,
  required,
  disabled,
  placeholder,
  minMm,
  maxMm,
  nonNegative = true,
  hideMmHint = false,
  className,
  name,
  autoFocus,
}: LengthInputProps): JSX.Element {
  const [text, setText] = useState(() => displayText(valueMm, display));
  const [error, setError] = useState<string | undefined>(undefined);
  const [editing, setEditing] = useState(false);

  // Re-sync when the model changes underneath us (undo, copilot, solver apply)
  // — but never while the user is mid-edit, which would eat their keystrokes.
  useEffect(() => {
    if (editing) return;
    setText(displayText(valueMm, display));
    setError(undefined);
  }, [valueMm, display, editing]);

  const commit = (raw: string): void => {
    const trimmed = raw.trim();
    if (trimmed === '') {
      // Empty means "no change" rather than "zero": clearing a wall length by
      // deleting the text would otherwise collapse the wall.
      setText(displayText(valueMm, display));
      setError(undefined);
      return;
    }
    const parsed = tryParseLengthMm(trimmed, bareUnit ?? display);
    if (!parsed.ok) {
      setError(`We couldn't read that. Try ${LENGTH_EXAMPLES[display]}.`);
      return;
    }
    const mm = parsed.mm;
    if (nonNegative && mm < 0) {
      setError('This needs to be a positive length.');
      return;
    }
    if (minMm !== undefined && mm < minMm) {
      setError(`Needs to be at least ${formatLength(minMm, display)} (${formatIndianNumber(minMm)} mm).`);
      return;
    }
    if (maxMm !== undefined && mm > maxMm) {
      setError(`Needs to be ${formatLength(maxMm, display)} (${formatIndianNumber(maxMm)} mm) or less.`);
      return;
    }
    setError(undefined);
    setText(displayText(mm, display));
    if (mm !== valueMm) onCommitMm(mm);
  };

  const live = tryParseLengthMm(text.trim() === '' ? '0' : text.trim(), bareUnit ?? display);
  const mmHint =
    hideMmHint || !live.ok || text.trim() === ''
      ? undefined
      : `= ${formatIndianNumber(live.mm)} mm`;

  return (
    <Field
      label={label}
      labelHidden={labelHidden}
      required={required}
      hint={hint ?? mmHint}
      error={error}
      className={className}
    >
      {({ id, describedBy, invalid }) => (
        <Input
          id={id}
          name={name}
          type="text"
          inputMode="text"
          autoComplete="off"
          spellCheck={false}
          autoFocus={autoFocus}
          disabled={disabled}
          placeholder={placeholder ?? (display === 'ft-in' ? `12'-6"` : '3.80 m')}
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
              setText(displayText(valueMm, display));
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
// PhoneInput — +91-aware (§15 "Indian defaults")
// ---------------------------------------------------------------------------

/**
 * Strip everything but digits and drop the dialling prefixes a user re-types.
 *
 * The three prefixes handled are the three that actually get pasted in India:
 * `00` (the international exit code, as in "0091 98765 43210"), `91` (the
 * country code, from a WhatsApp contact card), and the leading `0` from a
 * landline-style entry. Each is stripped ONLY when the number is longer than
 * ten digits, which is what keeps a genuine number beginning 91 — 9188888888 is
 * a valid mobile — from being mutilated into 88888888.
 */
export function normaliseIndianMobile(raw: string): string {
  let digits = raw.replace(/\D+/g, '');
  if (digits.startsWith('00') && digits.length > 10) digits = digits.slice(2);
  if (digits.startsWith('91') && digits.length > 10) digits = digits.slice(2);
  if (digits.startsWith('0') && digits.length > 10) digits = digits.slice(1);
  return digits.slice(0, 10);
}

/** `98765 43210` — the way an Indian mobile number is read aloud. */
export function formatIndianMobile(digits: string): string {
  const d = normaliseIndianMobile(digits);
  return d.length > 5 ? `${d.slice(0, 5)} ${d.slice(5)}` : d;
}

export function isPlausibleIndianMobile(digits: string): boolean {
  const d = normaliseIndianMobile(digits);
  return /^[6-9]\d{9}$/.test(d);
}

export interface PhoneInputProps {
  /** Ten digits, no country code. */
  value: string;
  onChange: (digits: string) => void;
  label?: string | undefined;
  hint?: ReactNode | undefined;
  error?: string | undefined;
  required?: boolean | undefined;
  disabled?: boolean | undefined;
  name?: string | undefined;
  className?: string | undefined;
}

/**
 * A +91 mobile field with the country code as a fixed, non-editable prefix.
 * Only Indian numbers are in scope for the MVP, so a country picker would be
 * three clicks of theatre.
 */
export function PhoneInput({
  value,
  onChange,
  label = 'Mobile number',
  hint,
  error,
  required,
  disabled,
  name,
  className,
}: PhoneInputProps): JSX.Element {
  return (
    <Field label={label} hint={hint} error={error} required={required} className={className}>
      {({ id, describedBy, invalid }) => (
        <div className="flex items-stretch">
          <span
            className={cn(
              'inline-flex h-9 items-center rounded-l-md border border-r-0 border-line-strong',
              'bg-surface-muted px-2.5 text-sm text-ink-muted garh-nums',
            )}
            aria-hidden="true"
          >
            +91
          </span>
          <Input
            id={id}
            name={name}
            type="tel"
            inputMode="numeric"
            autoComplete="tel-national"
            maxLength={11}
            disabled={disabled}
            placeholder="98765 43210"
            value={formatIndianMobile(value)}
            aria-describedby={describedBy}
            invalid={invalid}
            className="rounded-l-none garh-nums"
            onChange={(e) => onChange(normaliseIndianMobile(e.target.value))}
          />
        </div>
      )}
    </Field>
  );
}

// ---------------------------------------------------------------------------
// OtpInput — six boxes, paste-aware
// ---------------------------------------------------------------------------

export interface OtpInputProps {
  value: string;
  onChange: (code: string) => void;
  /** Fired when the last box is filled — lets the form submit itself. */
  onComplete?: ((code: string) => void) | undefined;
  length?: number | undefined;
  disabled?: boolean | undefined;
  label?: string | undefined;
  error?: string | undefined;
  autoFocus?: boolean | undefined;
}

/**
 * A single text input styled as N boxes.
 *
 * Deliberately NOT N separate inputs: the multi-input version breaks paste,
 * breaks password managers, breaks Android autofill of the SMS code, and needs
 * bespoke arrow-key handling. One input with letter-spacing gets all of that
 * right and stays one tab stop.
 */
export function OtpInput({
  value,
  onChange,
  onComplete,
  length = 6,
  disabled,
  label = 'Verification code',
  error,
  autoFocus,
}: OtpInputProps): JSX.Element {
  return (
    <Field label={label} error={error} hint={`${length} digits, from the email we just sent.`}>
      {({ id, describedBy, invalid }) => (
        <input
          id={id}
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="[0-9]*"
          maxLength={length}
          disabled={disabled}
          autoFocus={autoFocus}
          value={value}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          onChange={(e) => {
            const digits = e.target.value.replace(/\D+/g, '').slice(0, length);
            onChange(digits);
            if (digits.length === length) onComplete?.(digits);
          }}
          className={cn(
            'garh-focus-ring h-14 w-full rounded-md border border-line-strong bg-surface',
            'text-center text-2xl font-semibold tracking-[0.6em] text-ink garh-nums',
            'placeholder:tracking-[0.6em] placeholder:text-ink-subtle',
            invalid && CONTROL_INVALID_CLASS,
          )}
          placeholder={'·'.repeat(length)}
        />
      )}
    </Field>
  );
}
