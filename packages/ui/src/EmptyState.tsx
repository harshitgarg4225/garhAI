/**
 * EmptyState — golden rule 8, encoded in a type.
 *
 * "Every screen's empty state shows what to do next and offers the seeded demo
 * project." That is easy to agree with and easy to forget, so `demoAction` is a
 * REQUIRED prop. If a particular empty state genuinely cannot offer the demo
 * (you are already inside the demo project, say), you must pass
 * `demoAction={{ notApplicable: 'why' }}` and write the reason down. The
 * compiler will not let you simply omit it.
 *
 * Tone (§15): the title says what is missing in plain words, the description
 * says what to do next, and neither blames the user. "No projects yet" — not
 * "You have not created any projects".
 */

import type { ReactNode } from 'react';
import { cn } from './cn';
import { Button } from './Button';
import { Icon } from './icons';
import type { IconName } from './icons';

export interface EmptyStateAction {
  label: string;
  onClick: () => void;
  icon?: IconName | undefined;
  /** Disable while a create/seed request is in flight. */
  loading?: boolean | undefined;
}

/** The explicit opt-out from offering the demo project. */
export interface DemoNotApplicable {
  notApplicable: string;
}

export type EmptyStateDemo = EmptyStateAction | DemoNotApplicable;

function isNotApplicable(d: EmptyStateDemo): d is DemoNotApplicable {
  return 'notApplicable' in d;
}

export interface EmptyStateProps {
  icon?: IconName | undefined;
  /** What is missing. Short, plain. */
  title: string;
  /** What to do next. One or two sentences, warm, jargon-free. */
  description: ReactNode;
  /** The primary next action. Omit only for read-only surfaces. */
  action?: EmptyStateAction | undefined;
  /** A lesser alternative — "Import a DXF boundary". */
  secondaryAction?: EmptyStateAction | undefined;
  /**
   * REQUIRED. Either the "Try the demo project" action, or an explicit
   * `{ notApplicable: 'reason' }` opt-out. See golden rule 8.
   */
  demoAction: EmptyStateDemo;
  /** Extra content under the buttons — a tip list, a keyboard hint. */
  children?: ReactNode;
  /** Compact variant for panels and small tab bodies. */
  size?: 'md' | 'sm' | undefined;
  className?: string | undefined;
}

export function EmptyState({
  icon = 'sparkles',
  title,
  description,
  action,
  secondaryAction,
  demoAction,
  children,
  size = 'md',
  className,
}: EmptyStateProps): JSX.Element {
  const demo = isNotApplicable(demoAction) ? null : demoAction;

  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center text-center',
        size === 'md' ? 'gap-3 px-6 py-14' : 'gap-2 px-4 py-8',
        className,
      )}
    >
      <span
        className={cn(
          'flex items-center justify-center rounded-full bg-brand-soft text-brand-ink',
          size === 'md' ? 'h-12 w-12' : 'h-9 w-9',
        )}
        aria-hidden="true"
      >
        <Icon name={icon} size={size === 'md' ? 22 : 17} />
      </span>

      <div className="max-w-md">
        <h2 className={cn('font-semibold text-ink', size === 'md' ? 'text-base' : 'text-sm')}>{title}</h2>
        <p className={cn('mt-1 text-ink-muted', size === 'md' ? 'text-sm leading-6' : 'text-xs leading-5')}>
          {description}
        </p>
      </div>

      {action !== undefined || secondaryAction !== undefined || demo !== null ? (
        <div className="mt-1 flex flex-wrap items-center justify-center gap-2">
          {action === undefined ? null : (
            <Button
              variant="primary"
              size={size === 'md' ? 'md' : 'sm'}
              iconLeft={action.icon}
              loading={action.loading}
              loadingLabel={`${action.label}…`}
              onClick={action.onClick}
            >
              {action.label}
            </Button>
          )}
          {secondaryAction === undefined ? null : (
            <Button
              variant="secondary"
              size={size === 'md' ? 'md' : 'sm'}
              iconLeft={secondaryAction.icon}
              loading={secondaryAction.loading}
              onClick={secondaryAction.onClick}
            >
              {secondaryAction.label}
            </Button>
          )}
          {demo === null ? null : (
            <Button
              variant="ghost"
              size={size === 'md' ? 'md' : 'sm'}
              iconLeft={demo.icon ?? 'play'}
              loading={demo.loading}
              loadingLabel="Opening the demo project…"
              onClick={demo.onClick}
            >
              {demo.label}
            </Button>
          )}
        </div>
      ) : null}

      {children}
    </div>
  );
}

/**
 * The standard demo action. Centralised so the wording ("Try the demo project")
 * is identical on every screen — §15 first-run flow depends on the user
 * recognising the same button everywhere.
 */
export function demoProjectAction(onClick: () => void, loading?: boolean): EmptyStateAction {
  return { label: 'Try the demo project', onClick, icon: 'play', loading };
}

/**
 * An honest "this arrives in a later phase" panel.
 *
 * Used by the Plan / 3D / Renders / Sheets tabs before their phase lands. The
 * point is to state the truth rather than mock up a fake canvas: a placeholder
 * that pretends to work costs more trust than an empty tab that says when the
 * real thing arrives.
 */
export function PhasePlaceholder({
  title,
  phase,
  delivers,
  icon = 'sparkles',
  children,
  demoAction,
  className,
}: {
  title: string;
  /** "Phase 4" — matches SKILL.md build phases. */
  phase: string;
  /** One sentence on what will be here. */
  delivers: string;
  icon?: IconName | undefined;
  children?: ReactNode;
  demoAction?: EmptyStateDemo | undefined;
  className?: string | undefined;
}): JSX.Element {
  return (
    <div
      className={cn(
        'flex h-full min-h-64 flex-col items-center justify-center gap-3 rounded-lg border border-dashed',
        'border-line-strong bg-surface-muted px-6 py-12 text-center',
        className,
      )}
    >
      <span
        className="flex h-11 w-11 items-center justify-center rounded-full bg-surface text-ink-subtle"
        aria-hidden="true"
      >
        <Icon name={icon} size={20} />
      </span>
      <div className="max-w-md">
        <h2 className="text-base font-semibold text-ink">{title}</h2>
        <p className="mt-1 text-sm leading-6 text-ink-muted">{delivers}</p>
        <p className="mt-2 text-xs font-medium uppercase tracking-wider text-ink-subtle">
          Arrives in {phase}
        </p>
      </div>
      {children}
      {demoAction !== undefined && !isNotApplicable(demoAction) ? (
        <Button variant="ghost" size="sm" iconLeft="play" onClick={demoAction.onClick}>
          {demoAction.label}
        </Button>
      ) : null}
    </div>
  );
}
