/**
 * Badge — a small non-interactive status label.
 *
 * The difference from Chip: a Chip is a thing you can act on (filter, remove,
 * fix, edit); a Badge is a fact you read. "Demo", "G+1", "Stale", "Draft".
 * Keeping them separate stops half the app rendering un-clickable chips that
 * look clickable.
 */

import type { ReactNode } from 'react';
import { cn } from './cn';
import { Icon } from './icons';
import type { IconName } from './icons';

export type BadgeTone = 'neutral' | 'brand' | 'pass' | 'warn' | 'fail' | 'info' | 'outline';

const TONES: Record<BadgeTone, string> = {
  neutral: 'bg-neutral-soft text-neutral-ink',
  brand: 'bg-brand-soft text-brand-ink',
  pass: 'bg-pass-soft text-pass-ink',
  warn: 'bg-warn-soft text-warn-ink',
  fail: 'bg-fail-soft text-fail-ink',
  info: 'bg-info-soft text-info-ink',
  outline: 'border border-line-strong text-ink-muted',
};

export interface BadgeProps {
  children: ReactNode;
  tone?: BadgeTone | undefined;
  icon?: IconName | undefined;
  /** Small filled dot before the label — for live/queued/running states. */
  dot?: boolean | undefined;
  className?: string | undefined;
  title?: string | undefined;
  /** Accepted so a wrapping `<Tooltip>` can describe this badge — see Tooltip. */
  'aria-describedby'?: string | undefined;
}

export function Badge({
  children,
  tone = 'neutral',
  icon,
  dot,
  className,
  title,
  'aria-describedby': ariaDescribedBy,
}: BadgeProps): JSX.Element {
  return (
    <span
      title={title}
      aria-describedby={ariaDescribedBy}
      className={cn(
        'inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-wide',
        TONES[tone],
        className,
      )}
    >
      {dot === true ? (
        <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />
      ) : null}
      {icon === undefined ? null : <Icon name={icon} size={11} />}
      {children}
    </span>
  );
}

/** Numeric counter for tabs and queues: "3". Zero renders nothing. */
export function CountBadge({
  count,
  tone = 'neutral',
}: {
  count: number;
  tone?: BadgeTone;
}): JSX.Element | null {
  if (count <= 0) return null;
  return (
    <span
      className={cn(
        'inline-flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-2xs font-semibold garh-nums',
        TONES[tone],
      )}
    >
      {count > 99 ? '99+' : count}
    </span>
  );
}
