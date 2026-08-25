/**
 * AppShell + ProjectLayout — the two page frames.
 *
 * `AppShell` is the chrome outside a project: the dashboard, settings, the
 * account menu. `ProjectLayout` is the §12 panel grid:
 *
 *     ┌─────────────────────────────────────────────┐
 *     │ TopBar                                      │  h-topbar
 *     ├───┬─────────────────────────────┬───────────┤
 *     │ r │                             │           │
 *     │ a │  canvas / tab body          │ Inspector │  flex-1, min-h-0
 *     │ i │                             │           │
 *     │ l │                             │           │
 *     ├───┴─────────────────────────────┴───────────┤
 *     │ ComplianceStrip                             │  h-strip
 *     └─────────────────────────────────────────────┘
 *
 * The `min-h-0` on the middle row is load-bearing: without it a flex child with
 * scrolling content refuses to shrink and the compliance strip gets pushed off
 * the bottom of the viewport.
 *
 * Both frames render their own skip link. A canvas app with a tool rail is
 * exactly the kind of page where tabbing to the content takes twenty presses.
 */

import type { ReactNode } from 'react';
import { Icon, IconButton, Tooltip, cn } from '@garh/ui';

// ---------------------------------------------------------------------------
// AppShell
// ---------------------------------------------------------------------------

export interface AppShellProps {
  /** Firm name, shown next to the wordmark. */
  firmName?: string | undefined;
  /** Signed-in user's display name, for the account button. */
  userName?: string | undefined;
  onSignOut?: (() => void) | undefined;
  /** Router link for the wordmark. Defaults to a plain anchor to "/". */
  renderHomeLink?:
    | ((props: { className: string; children: ReactNode }) => ReactNode)
    | undefined;
  /** Right-aligned header slot: search, "New project", theme toggle. */
  headerActions?: ReactNode | undefined;
  children: ReactNode;
}

export function AppShell({
  firmName,
  userName,
  onSignOut,
  renderHomeLink,
  headerActions,
  children,
}: AppShellProps): JSX.Element {
  const wordmark = (
    <>
      <span
        className="flex h-7 w-7 items-center justify-center rounded-md bg-brand text-brand-fg"
        aria-hidden="true"
      >
        <Icon name="home" size={16} />
      </span>
      <span className="text-sm font-semibold text-ink">Garh AI</span>
      {firmName === undefined ? null : (
        <>
          <span className="text-ink-subtle" aria-hidden="true">
            /
          </span>
          <span className="truncate text-sm text-ink-muted">{firmName}</span>
        </>
      )}
    </>
  );

  const homeClass = 'garh-focus-ring flex items-center gap-2 rounded-md no-underline';

  return (
    <div className="flex min-h-screen flex-col bg-canvas">
      <SkipLink />
      <header className="sticky top-0 z-topbar flex h-topbar shrink-0 items-center gap-3 border-b border-line bg-surface px-4">
        {renderHomeLink !== undefined ? (
          renderHomeLink({ className: homeClass, children: wordmark })
        ) : (
          <a href="/" className={homeClass}>
            {wordmark}
          </a>
        )}

        <div className="ml-auto flex items-center gap-2">
          {headerActions}
          {userName === undefined ? null : (
            <Tooltip content={`Signed in as ${userName}`} delayMs={300}>
              <span className="flex h-8 items-center gap-2 rounded-full bg-surface-muted px-2.5 text-xs text-ink-muted">
                <Icon name="user" size={14} />
                <span className="max-w-[10rem] truncate">{userName}</span>
              </span>
            </Tooltip>
          )}
          {onSignOut === undefined ? null : (
            <IconButton label="Sign out" icon="log-out" size="sm" onClick={onSignOut} />
          )}
        </div>
      </header>

      <main id="main" tabIndex={-1} className="flex-1 outline-none">
        {children}
      </main>
    </div>
  );
}

function SkipLink(): JSX.Element {
  return (
    <a
      href="#main"
      className={cn(
        'sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-toast',
        'focus:rounded-md focus:bg-surface focus:px-3 focus:py-2 focus:text-sm focus:text-ink focus:shadow-lg',
      )}
    >
      Skip to content
    </a>
  );
}

// ---------------------------------------------------------------------------
// ProjectLayout
// ---------------------------------------------------------------------------

export interface ProjectLayoutProps {
  /** <TopBar>. */
  topBar: ReactNode;
  /** Tab strip under the top bar (Brief · Plan · 3D · …). */
  tabs?: ReactNode | undefined;
  /** <SideRail>. Omit on tabs that are not the canvas (Brief, Sheets). */
  rail?: ReactNode | undefined;
  /** <Inspector>. Omit where there is nothing to inspect. */
  inspector?: ReactNode | undefined;
  /**
   * <CopilotPanel> — the §10 chat rail, docked outermost right.
   *
   * A slot of its own rather than "put it in `inspector`" because the two
   * coexist: on the Plan tab you inspect the wall the copilot just proposed
   * moving. It sits OUTSIDE the inspector so the reading order is
   * canvas → properties → conversation, and the panel itself renders `null`
   * while `ui.copilotOpen` is false, so the shell mounts it unconditionally and
   * the grid does not reflow around a conditional child.
   */
  copilot?: ReactNode | undefined;
  /** <ComplianceStrip>. */
  complianceStrip?: ReactNode | undefined;
  /** The tab body. Scrolls independently. */
  children: ReactNode;
  /** Set false for tabs that should not scroll (the canvas owns its viewport). */
  scrollBody?: boolean | undefined;
}

export function ProjectLayout({
  topBar,
  tabs,
  rail,
  inspector,
  copilot,
  complianceStrip,
  children,
  scrollBody = true,
}: ProjectLayoutProps): JSX.Element {
  return (
    <div className="flex h-screen flex-col overflow-hidden bg-canvas">
      <SkipLink />
      {topBar}
      {tabs === undefined ? null : (
        <div className="shrink-0 border-b border-line bg-surface px-3">{tabs}</div>
      )}

      <div className="flex min-h-0 flex-1">
        {rail}
        <main
          id="main"
          tabIndex={-1}
          className={cn('min-w-0 flex-1 outline-none', scrollBody ? 'overflow-y-auto' : 'overflow-hidden')}
        >
          {children}
        </main>
        {inspector}
        {copilot}
      </div>

      {complianceStrip}
    </div>
  );
}

/**
 * A centred single-column page body — used by the dashboard, settings and the
 * non-canvas project tabs so their max width matches.
 */
export function PageBody({
  className,
  children,
}: {
  className?: string | undefined;
  children: ReactNode;
}): JSX.Element {
  return <div className={cn('mx-auto w-full max-w-6xl px-4 py-6', className)}>{children}</div>;
}

/** Page heading + description + actions. One shape for every screen. */
export function PageHeader({
  title,
  description,
  actions,
  className,
}: {
  title: string;
  description?: ReactNode | undefined;
  actions?: ReactNode | undefined;
  className?: string | undefined;
}): JSX.Element {
  return (
    <div className={cn('mb-5 flex flex-wrap items-end justify-between gap-3', className)}>
      <div className="min-w-0">
        <h1 className="text-lg font-semibold text-ink">{title}</h1>
        {description === undefined ? null : (
          <p className="mt-0.5 text-sm text-ink-muted">{description}</p>
        )}
      </div>
      {actions === undefined ? null : <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}
