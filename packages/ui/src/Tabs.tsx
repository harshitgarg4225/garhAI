/**
 * Tabs — WAI-ARIA tab pattern with roving tabindex.
 *
 * Keyboard: ←/→ move (wrapping), Home/End jump to the ends, and the focused tab
 * activates immediately (automatic activation) because none of our panels are
 * expensive enough to justify manual activation's extra Enter press.
 *
 * `TabLinks` is the routing variant used by the project shell: same look, but
 * each tab is an anchor so middle-click, ⌘-click and browser history all work.
 * It intentionally does NOT use role="tab" — a set of links that change the URL
 * is a navigation landmark, not a tablist, and announcing it as tabs would lie
 * about the back button.
 */

import { useRef } from 'react';
import type { ReactNode } from 'react';
import { cn } from './cn';
import { Icon } from './icons';
import type { IconName } from './icons';
import { CountBadge } from './Badge';

export interface TabItem<T extends string = string> {
  value: T;
  label: string;
  icon?: IconName | undefined;
  count?: number | undefined;
  disabled?: boolean | undefined;
  /** Small dot marking "there is something new here". */
  attention?: boolean | undefined;
}

export type TabsVariant = 'underline' | 'pill';

export interface TabsProps<T extends string = string> {
  items: ReadonlyArray<TabItem<T>>;
  value: T;
  onValueChange: (value: T) => void;
  /** Accessible name for the tablist, e.g. "Project sections". */
  label: string;
  variant?: TabsVariant | undefined;
  className?: string | undefined;
}

const TAB_BASE =
  'garh-focus-ring relative inline-flex items-center gap-1.5 whitespace-nowrap text-sm font-medium ' +
  'transition-colors disabled:cursor-not-allowed disabled:opacity-45';

const VARIANT_LIST: Record<TabsVariant, string> = {
  underline: 'flex items-stretch gap-1 border-b border-line',
  pill: 'inline-flex items-center gap-1 rounded-lg bg-surface-muted p-1',
};

const VARIANT_TAB: Record<TabsVariant, { base: string; on: string; off: string }> = {
  underline: {
    base: 'h-10 rounded-t-md px-3',
    on: 'text-ink after:absolute after:inset-x-1 after:-bottom-px after:h-0.5 after:rounded-full after:bg-brand',
    off: 'text-ink-muted hover:text-ink',
  },
  pill: {
    base: 'h-8 rounded-md px-3',
    on: 'bg-surface text-ink shadow-sm',
    off: 'text-ink-muted hover:text-ink',
  },
};

export function Tabs<T extends string>({
  items,
  value,
  onValueChange,
  label,
  variant = 'underline',
  className,
}: TabsProps<T>): JSX.Element {
  const listRef = useRef<HTMLDivElement>(null);

  const move = (delta: number, from: number): void => {
    const enabled = items.map((it, i) => ({ it, i })).filter((x) => x.it.disabled !== true);
    if (enabled.length === 0) return;
    const pos = enabled.findIndex((x) => x.i === from);
    const nextPos = (pos + delta + enabled.length) % enabled.length;
    const target = enabled[nextPos];
    if (target === undefined) return;
    onValueChange(target.it.value);
    const node = listRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[target.i];
    node?.focus();
  };

  const jump = (to: 'first' | 'last'): void => {
    const enabled = items.map((it, i) => ({ it, i })).filter((x) => x.it.disabled !== true);
    const target = to === 'first' ? enabled[0] : enabled[enabled.length - 1];
    if (target === undefined) return;
    onValueChange(target.it.value);
    listRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[target.i]?.focus();
  };

  return (
    <div ref={listRef} role="tablist" aria-label={label} className={cn(VARIANT_LIST[variant], className)}>
      {items.map((item, index) => {
        const selected = item.value === value;
        const skin = VARIANT_TAB[variant];
        return (
          <button
            key={item.value}
            type="button"
            role="tab"
            id={`tab-${item.value}`}
            aria-selected={selected}
            aria-controls={`tabpanel-${item.value}`}
            tabIndex={selected ? 0 : -1}
            disabled={item.disabled}
            onClick={() => onValueChange(item.value)}
            onKeyDown={(e) => {
              if (e.key === 'ArrowRight') {
                e.preventDefault();
                move(1, index);
              } else if (e.key === 'ArrowLeft') {
                e.preventDefault();
                move(-1, index);
              } else if (e.key === 'Home') {
                e.preventDefault();
                jump('first');
              } else if (e.key === 'End') {
                e.preventDefault();
                jump('last');
              }
            }}
            className={cn(TAB_BASE, skin.base, selected ? skin.on : skin.off)}
          >
            {item.icon === undefined ? null : <Icon name={item.icon} size={15} />}
            {item.label}
            {item.count === undefined ? null : <CountBadge count={item.count} />}
            {item.attention === true ? (
              <span className="h-1.5 w-1.5 rounded-full bg-brand" aria-label="Needs attention" role="img" />
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

export function TabPanel<T extends string>({
  value,
  active,
  className,
  children,
}: {
  value: T;
  active: boolean;
  className?: string | undefined;
  children: ReactNode;
}): JSX.Element | null {
  if (!active) return null;
  return (
    <div
      role="tabpanel"
      id={`tabpanel-${value}`}
      aria-labelledby={`tab-${value}`}
      tabIndex={0}
      className={cn('garh-focus-ring outline-none', className)}
    >
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// TabLinks — the routed variant
// ---------------------------------------------------------------------------

export interface TabLinkItem {
  key: string;
  label: string;
  href: string;
  icon?: IconName | undefined;
  count?: number | undefined;
  attention?: boolean | undefined;
  /** Phase-gated tabs stay visible but say so rather than vanishing. */
  disabled?: boolean | undefined;
  disabledReason?: string | undefined;
}

export interface TabLinksProps {
  items: readonly TabLinkItem[];
  activeKey: string;
  label: string;
  variant?: TabsVariant | undefined;
  /**
   * Render an anchor. The web app passes react-router's <Link> so navigation
   * stays client-side; the default is a plain <a> so this package has no
   * router dependency.
   */
  renderLink?:
    | ((props: { href: string; className: string; children: ReactNode; 'aria-current': 'page' | undefined }) => ReactNode)
    | undefined;
  className?: string | undefined;
}

export function TabLinks({
  items,
  activeKey,
  label,
  variant = 'underline',
  renderLink,
  className,
}: TabLinksProps): JSX.Element {
  const skin = VARIANT_TAB[variant];
  return (
    <nav aria-label={label} className={cn(VARIANT_LIST[variant], className)}>
      {items.map((item) => {
        const active = item.key === activeKey;
        const classes = cn(TAB_BASE, skin.base, active ? skin.on : skin.off, item.disabled === true && 'opacity-45');
        const body = (
          <>
            {item.icon === undefined ? null : <Icon name={item.icon} size={15} />}
            {item.label}
            {item.count === undefined ? null : <CountBadge count={item.count} />}
            {item.attention === true ? (
              <span className="h-1.5 w-1.5 rounded-full bg-brand" aria-hidden="true" />
            ) : null}
          </>
        );

        if (item.disabled === true) {
          return (
            <span
              key={item.key}
              className={cn(classes, 'cursor-not-allowed')}
              title={item.disabledReason}
              aria-disabled="true"
            >
              {body}
            </span>
          );
        }

        if (renderLink !== undefined) {
          return (
            <span key={item.key} className="contents">
              {renderLink({
                href: item.href,
                className: classes,
                children: body,
                'aria-current': active ? 'page' : undefined,
              })}
            </span>
          );
        }

        return (
          <a
            key={item.key}
            href={item.href}
            aria-current={active ? 'page' : undefined}
            className={cn(classes, 'no-underline')}
          >
            {body}
          </a>
        );
      })}
    </nav>
  );
}
