/**
 * Card — the surface every list item, panel section and option tile sits on.
 *
 * `interactive` renders a real <button>/<a> rather than a div with an onClick,
 * so the whole card is one tab stop with a proper role. Nested interactive
 * elements inside an interactive card are a known a11y trap — put row actions
 * in the `actions` slot of CardHeader on a NON-interactive card instead.
 */

import { forwardRef } from 'react';
import type { AnchorHTMLAttributes, HTMLAttributes, ReactNode } from 'react';
import { cn } from './cn';

const SHELL = 'rounded-lg border border-line bg-surface';
const INTERACTIVE =
  'garh-focus-ring block w-full text-left transition-shadow hover:border-line-strong hover:shadow-md ' +
  'active:shadow-sm';

export interface CardProps extends Omit<HTMLAttributes<HTMLDivElement>, 'className'> {
  className?: string | undefined;
  /** Removes the border/background — for grouping without visual weight. */
  flush?: boolean | undefined;
  children?: ReactNode;
}

export const Card = forwardRef<HTMLDivElement, CardProps>(function Card(
  { className, flush = false, children, ...rest },
  ref,
) {
  return (
    <div ref={ref} className={cn(flush ? '' : SHELL, className)} {...rest}>
      {children}
    </div>
  );
});

export interface CardLinkProps extends Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'className'> {
  className?: string | undefined;
  children?: ReactNode;
}

/** A card that navigates. Use for project tiles, option tiles, sheet tiles. */
export const CardLink = forwardRef<HTMLAnchorElement, CardLinkProps>(function CardLink(
  { className, children, ...rest },
  ref,
) {
  return (
    <a ref={ref} className={cn(SHELL, INTERACTIVE, 'no-underline', className)} {...rest}>
      {children}
    </a>
  );
});

export function CardHeader({
  title,
  description,
  actions,
  className,
}: {
  title: ReactNode;
  description?: ReactNode | undefined;
  actions?: ReactNode | undefined;
  className?: string | undefined;
}): JSX.Element {
  return (
    <div className={cn('flex items-start justify-between gap-3 px-4 pb-3 pt-3.5', className)}>
      <div className="min-w-0">
        <h3 className="truncate text-sm font-semibold text-ink">{title}</h3>
        {description === undefined ? null : (
          <p className="mt-0.5 text-xs leading-4 text-ink-muted">{description}</p>
        )}
      </div>
      {actions === undefined ? null : <div className="flex shrink-0 items-center gap-1">{actions}</div>}
    </div>
  );
}

export function CardBody({
  className,
  children,
}: {
  className?: string | undefined;
  children: ReactNode;
}): JSX.Element {
  return <div className={cn('px-4 pb-4', className)}>{children}</div>;
}

export function CardFooter({
  className,
  children,
}: {
  className?: string | undefined;
  children: ReactNode;
}): JSX.Element {
  return (
    <div
      className={cn(
        'flex items-center justify-between gap-2 border-t border-line bg-surface-muted px-4 py-2.5',
        className,
      )}
    >
      {children}
    </div>
  );
}

/**
 * A labelled group inside the inspector. Not a Card — the inspector is already
 * a surface, and nesting borders three deep is how panels start to look like a
 * 2004 control panel.
 */
export function PanelSection({
  title,
  actions,
  className,
  children,
}: {
  title: string;
  actions?: ReactNode | undefined;
  className?: string | undefined;
  children: ReactNode;
}): JSX.Element {
  return (
    <section className={cn('border-b border-line px-3 py-3 last:border-b-0', className)}>
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="text-2xs font-semibold uppercase tracking-wider text-ink-subtle">{title}</h3>
        {actions}
      </div>
      {children}
    </section>
  );
}

/** Label/value row for the inspector and area statements. */
export function DataRow({
  label,
  value,
  hint,
  className,
}: {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode | undefined;
  className?: string | undefined;
}): JSX.Element {
  return (
    <div className={cn('flex items-baseline justify-between gap-3 py-1', className)}>
      <span className="text-xs text-ink-muted">{label}</span>
      <span className="text-right text-xs font-medium text-ink garh-nums">
        {value}
        {hint === undefined ? null : (
          <span className="ml-1 font-normal text-ink-subtle">{hint}</span>
        )}
      </span>
    </div>
  );
}
