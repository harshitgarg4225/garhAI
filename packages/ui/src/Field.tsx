/**
 * Field — the label / description / error scaffold every form control sits in.
 *
 * Having one component own this is what makes the a11y wiring correct by
 * default: the label's `htmlFor`, the `aria-describedby` chain (hint AND error,
 * in that order), `aria-invalid`, and the required marker are derived once here
 * instead of being re-typed (and half-forgotten) at 40 call sites.
 *
 * Error copy convention (§15, "never blame the user"): the message says what we
 * could not do and what would work — "We couldn't read that length. Try 12'6\",
 * 3.8m or 3810." — never "Invalid input".
 */

import { useId } from 'react';
import type { ReactNode } from 'react';
import { cn } from './cn';
import { Icon } from './icons';

export interface FieldRenderArgs {
  id: string;
  describedBy: string | undefined;
  invalid: boolean;
}

export interface FieldProps {
  label: string;
  /** Hide the label visually but keep it for screen readers. */
  labelHidden?: boolean | undefined;
  /** Quiet helper text under the control. */
  hint?: ReactNode | undefined;
  /** When set the field renders as invalid and the message replaces the hint. */
  error?: string | undefined;
  required?: boolean | undefined;
  /** Right-aligned adornment on the label row (e.g. a units toggle). */
  action?: ReactNode | undefined;
  className?: string | undefined;
  children: (args: FieldRenderArgs) => ReactNode;
}

export function Field({
  label,
  labelHidden = false,
  hint,
  error,
  required = false,
  action,
  className,
  children,
}: FieldProps): JSX.Element {
  const base = useId();
  const id = `${base}-control`;
  const hintId = `${base}-hint`;
  const errorId = `${base}-error`;
  const invalid = error !== undefined && error !== '';

  const described = [hint !== undefined && hint !== null ? hintId : null, invalid ? errorId : null]
    .filter((x): x is string => x !== null)
    .join(' ');

  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <div className={cn('flex items-baseline justify-between gap-3', labelHidden && 'sr-only')}>
        <label htmlFor={id} className="text-xs font-medium text-ink-muted">
          {label}
          {required ? (
            <span className="ml-0.5 text-fail-ink" aria-hidden="true">
              *
            </span>
          ) : null}
        </label>
        {action}
      </div>

      {children({ id, describedBy: described === '' ? undefined : described, invalid })}

      {hint !== undefined && hint !== null ? (
        <p id={hintId} className="text-2xs leading-4 text-ink-subtle">
          {hint}
        </p>
      ) : null}

      {invalid ? (
        <p id={errorId} role="alert" className="flex items-start gap-1.5 text-2xs leading-4 text-fail-ink">
          <Icon name="alert-circle" size={13} className="mt-px" />
          <span>{error}</span>
        </p>
      ) : null}
    </div>
  );
}

/** Shared control skin — used by Input, Select and the length editor. */
export const CONTROL_CLASS =
  'garh-focus-ring h-9 w-full rounded-md border border-line-strong bg-surface px-2.5 text-sm ' +
  'text-ink placeholder:text-ink-subtle transition-colors ' +
  'hover:border-ink-subtle disabled:cursor-not-allowed disabled:bg-surface-muted disabled:text-ink-subtle';

export const CONTROL_INVALID_CLASS = 'border-fail text-fail-ink hover:border-fail';
