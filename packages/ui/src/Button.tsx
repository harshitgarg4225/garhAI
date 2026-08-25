/**
 * Button / IconButton — every clickable affordance in the product.
 *
 * Accessibility contract:
 *  - Always a real <button> (or <a> via `as="a"`), so Space/Enter, focus order
 *    and screen-reader roles come for free.
 *  - `loading` sets `aria-busy` and disables activation WITHOUT setting the
 *    `disabled` attribute, because a disabled button drops out of the tab order
 *    mid-interaction and strands keyboard users.
 *  - IconButton REQUIRES `label`; there is no way to ship an unlabelled icon
 *    button through this API.
 */

import { forwardRef } from 'react';
import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from 'react';
import { cn } from './cn';
import { Icon, Spinner } from './icons';
import type { IconName } from './icons';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'subtle' | 'danger' | 'link';
export type ButtonSize = 'sm' | 'md' | 'lg';

const BASE =
  'garh-focus-ring relative inline-flex select-none items-center justify-center gap-2 whitespace-nowrap ' +
  'rounded-md font-medium transition-colors duration-100 disabled:pointer-events-none disabled:opacity-45';

const VARIANTS: Record<ButtonVariant, string> = {
  primary: 'bg-brand text-brand-fg hover:bg-brand-strong active:bg-brand-strong shadow-sm',
  secondary:
    'border border-line-strong bg-surface text-ink hover:bg-surface-muted active:bg-surface-muted',
  ghost: 'text-ink-muted hover:bg-surface-muted hover:text-ink',
  subtle: 'bg-surface-muted text-ink hover:bg-line',
  danger: 'bg-fail text-white hover:opacity-90 active:opacity-100 shadow-sm dark:text-ink-inverse',
  link: 'text-brand-ink underline underline-offset-2 hover:text-brand rounded-sm',
};

const SIZES: Record<ButtonSize, string> = {
  sm: 'h-8 px-2.5 text-xs',
  md: 'h-9 px-3.5 text-sm',
  lg: 'h-11 px-5 text-[0.9375rem]',
};

interface CommonButtonProps {
  variant?: ButtonVariant | undefined;
  size?: ButtonSize | undefined;
  /** Leading icon. Decorative — the label carries the meaning. */
  iconLeft?: IconName | undefined;
  iconRight?: IconName | undefined;
  /** Shows a spinner and blocks activation, but keeps the button focusable. */
  loading?: boolean | undefined;
  /** Announced while `loading` — say what is happening, not "loading". */
  loadingLabel?: string | undefined;
  fullWidth?: boolean | undefined;
  className?: string | undefined;
  children?: ReactNode;
}

export interface ButtonProps
  extends CommonButtonProps,
    Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children' | 'className'> {}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'secondary',
    size = 'md',
    iconLeft,
    iconRight,
    loading = false,
    loadingLabel,
    fullWidth = false,
    className,
    children,
    type = 'button',
    onClick,
    ...rest
  },
  ref,
) {
  const iconSize = size === 'lg' ? 18 : 16;
  return (
    <button
      ref={ref}
      type={type}
      aria-busy={loading || undefined}
      /*
       * While loading we must block the button's DEFAULT action as well as the
       * handler. Dropping `onClick` alone leaves `type="submit"` submitting its
       * form on every further click — which on the sign-in screen means a second
       * OTP email while the first request is still in flight. `preventDefault`
       * stops the submit without setting `disabled`, which would pull the button
       * out of the tab order mid-interaction (see the accessibility note above).
       */
      onClick={loading ? (event) => event.preventDefault() : onClick}
      className={cn(
        BASE,
        VARIANTS[variant],
        SIZES[size],
        loading && 'cursor-progress',
        fullWidth && 'w-full',
        className,
      )}
      {...rest}
    >
      {loading ? (
        <Spinner size={iconSize} />
      ) : iconLeft !== undefined ? (
        <Icon name={iconLeft} size={iconSize} />
      ) : null}
      {children}
      {!loading && iconRight !== undefined ? <Icon name={iconRight} size={iconSize} /> : null}
      {loading && loadingLabel !== undefined ? (
        <span className="sr-only" role="status">
          {loadingLabel}
        </span>
      ) : null}
    </button>
  );
});

export interface LinkButtonProps
  extends CommonButtonProps,
    Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'children' | 'className'> {}

/** Same skin as Button, but a real anchor — use whenever it navigates. */
export const LinkButton = forwardRef<HTMLAnchorElement, LinkButtonProps>(function LinkButton(
  {
    variant = 'secondary',
    size = 'md',
    iconLeft,
    iconRight,
    loading = false,
    loadingLabel: _loadingLabel,
    fullWidth = false,
    className,
    children,
    ...rest
  },
  ref,
) {
  const iconSize = size === 'lg' ? 18 : 16;
  return (
    <a
      ref={ref}
      className={cn(
        BASE,
        VARIANTS[variant],
        SIZES[size],
        fullWidth && 'w-full',
        'no-underline',
        className,
      )}
      {...rest}
    >
      {loading ? (
        <Spinner size={iconSize} />
      ) : iconLeft !== undefined ? (
        <Icon name={iconLeft} size={iconSize} />
      ) : null}
      {children}
      {iconRight !== undefined ? <Icon name={iconRight} size={iconSize} /> : null}
    </a>
  );
});

export interface IconButtonProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children' | 'className'> {
  /** REQUIRED accessible name. Also used as the tooltip text by callers. */
  label: string;
  icon: IconName;
  variant?: ButtonVariant | undefined;
  size?: ButtonSize | undefined;
  /** Renders the pressed state of a toggle button and sets aria-pressed. */
  pressed?: boolean | undefined;
  loading?: boolean | undefined;
  className?: string | undefined;
}

const ICON_SIZES: Record<ButtonSize, string> = {
  sm: 'h-7 w-7',
  md: 'h-9 w-9',
  lg: 'h-11 w-11',
};

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, icon, variant = 'ghost', size = 'md', pressed, loading = false, className, type = 'button', ...rest },
  ref,
) {
  const glyph = size === 'lg' ? 20 : size === 'sm' ? 15 : 17;
  return (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      aria-pressed={pressed}
      aria-busy={loading || undefined}
      className={cn(
        BASE,
        VARIANTS[variant],
        ICON_SIZES[size],
        'p-0',
        pressed === true && 'bg-brand-soft text-brand-ink hover:bg-brand-soft',
        className,
      )}
      {...rest}
    >
      {loading ? <Spinner size={glyph} /> : <Icon name={icon} size={glyph} />}
    </button>
  );
});
