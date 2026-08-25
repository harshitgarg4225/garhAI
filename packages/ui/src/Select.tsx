/**
 * Select — a styled native <select>.
 *
 * Not a custom listbox. A native select is keyboard-operable, screen-reader
 * correct, type-ahead searchable and renders as the platform picker on a phone
 * — all things a hand-rolled popup gets wrong for months. The only thing we
 * lose is per-option iconography, which no select in this product needs.
 */

import { forwardRef } from 'react';
import type { ForwardedRef, ReactNode, SelectHTMLAttributes } from 'react';
import { cn } from './cn';
import { CONTROL_CLASS, CONTROL_INVALID_CLASS, Field } from './Field';
import { Icon } from './icons';

export interface SelectOption<T extends string = string> {
  value: T;
  label: string;
  disabled?: boolean | undefined;
  /** Renders an <optgroup>; consecutive options with the same group merge. */
  group?: string | undefined;
}

export interface SelectProps<T extends string = string>
  extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'className' | 'value' | 'onChange'> {
  value: T;
  onValueChange: (value: T) => void;
  options: ReadonlyArray<SelectOption<T>>;
  /** Rendered as a disabled first option when `value` is empty. */
  placeholder?: string | undefined;
  invalid?: boolean | undefined;
  className?: string | undefined;
}

function SelectInner<T extends string>(
  { value, onValueChange, options, placeholder, invalid = false, className, ...rest }: SelectProps<T>,
  ref: ForwardedRef<HTMLSelectElement>,
): JSX.Element {
  const groups: Array<{ group: string | undefined; items: Array<SelectOption<T>> }> = [];
  for (const opt of options) {
    const last = groups[groups.length - 1];
    if (last !== undefined && last.group === opt.group) last.items.push(opt);
    else groups.push({ group: opt.group, items: [opt] });
  }

  return (
    <div className="relative flex items-center">
      <select
        ref={ref}
        value={value}
        aria-invalid={invalid || undefined}
        onChange={(e) => onValueChange(e.target.value as T)}
        className={cn(
          CONTROL_CLASS,
          'cursor-pointer appearance-none pr-8',
          invalid && CONTROL_INVALID_CLASS,
          className,
        )}
        {...rest}
      >
        {placeholder !== undefined ? (
          <option value="" disabled>
            {placeholder}
          </option>
        ) : null}
        {groups.map((g, i) =>
          g.group === undefined ? (
            g.items.map((o) => (
              <option key={o.value} value={o.value} disabled={o.disabled}>
                {o.label}
              </option>
            ))
          ) : (
            <optgroup key={`${g.group}-${i}`} label={g.group}>
              {g.items.map((o) => (
                <option key={o.value} value={o.value} disabled={o.disabled}>
                  {o.label}
                </option>
              ))}
            </optgroup>
          ),
        )}
      </select>
      <Icon
        name="chevron-down"
        size={15}
        className="pointer-events-none absolute right-2.5 text-ink-subtle"
      />
    </div>
  );
}

export const Select = forwardRef(SelectInner) as <T extends string>(
  props: SelectProps<T> & { ref?: ForwardedRef<HTMLSelectElement> },
) => JSX.Element;

export interface SelectFieldProps<T extends string = string> extends SelectProps<T> {
  label: string;
  labelHidden?: boolean | undefined;
  hint?: ReactNode | undefined;
  error?: string | undefined;
  required?: boolean | undefined;
  fieldClassName?: string | undefined;
}

/** Select wrapped in the standard label/hint/error scaffold. */
export function SelectField<T extends string>({
  label,
  labelHidden,
  hint,
  error,
  required,
  fieldClassName,
  ...selectProps
}: SelectFieldProps<T>): JSX.Element {
  return (
    <Field
      label={label}
      labelHidden={labelHidden}
      hint={hint}
      error={error}
      required={required}
      className={fieldClassName}
    >
      {({ id, describedBy, invalid }) => (
        <Select<T> id={id} aria-describedby={describedBy} invalid={invalid} {...selectProps} />
      )}
    </Field>
  );
}
