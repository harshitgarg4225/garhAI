/**
 * TopBar — the project shell's header (§12).
 *
 * "top bar (project, storey tabs, units toggle, share, generate buttons)".
 * Undo/redo and the autosave badge sit here too, because §15 wants both
 * permanently visible: "everything undoable, visibly" and the "Saved · v214"
 * indicator.
 *
 * Storey tabs use the 1/2/3 shortcuts from the §12 keyboard map. They are
 * rendered as a radiogroup rather than ARIA tabs: switching storey does not
 * swap a panel, it changes what the same canvas shows.
 */

import { useEffect, useRef, useState } from 'react';
import type { UnitsDisplay } from '@garh/model';
import { Badge, Button, IconButton, ShortcutHint, Tooltip, cn } from '@garh/ui';
import { AutosaveBadge } from './AutosaveBadge';
import type { SaveState } from './AutosaveBadge';
import { UnitsToggle } from './UnitsToggle';

export interface StoreyTab {
  id: string;
  /** "Ground", "First", "Terrace". */
  label: string;
  /** 1-based shortcut digit; only the first nine get one. */
  shortcut?: string | undefined;
}

export interface TopBarProps {
  projectName: string;
  /** Inline rename. Omit to render the name as plain text. */
  onRename?: ((name: string) => void) | undefined;
  /** Second line: "Bengaluru · 30×40 ft · G+1". */
  subtitle?: string | undefined;
  isDemo?: boolean | undefined;

  storeys?: readonly StoreyTab[] | undefined;
  activeStoreyId?: string | undefined;
  onStoreyChange?: ((storeyId: string) => void) | undefined;

  units: UnitsDisplay;
  onUnitsChange: (units: UnitsDisplay) => void;

  saveState: SaveState;
  version?: number | undefined;
  pendingCount?: number | undefined;
  onSaveRecover?: (() => void) | undefined;

  canUndo?: boolean | undefined;
  canRedo?: boolean | undefined;
  onUndo?: (() => void) | undefined;
  onRedo?: (() => void) | undefined;

  /**
   * Show/hide the §10 copilot rail. Omit to leave the button out entirely —
   * a project surface with no copilot mounted should not advertise one.
   */
  copilotOpen?: boolean | undefined;
  onCopilotToggle?: (() => void) | undefined;

  onShare?: (() => void) | undefined;
  onGenerate?: (() => void) | undefined;
  generateLabel?: string | undefined;
  generateBusy?: boolean | undefined;
  /** Why generate is unavailable — shown on hover instead of a dead button. */
  generateDisabledReason?: string | undefined;

  /** Back to the dashboard. */
  onBack?: (() => void) | undefined;
  className?: string | undefined;
}

export function TopBar({
  projectName,
  onRename,
  subtitle,
  isDemo = false,
  storeys,
  activeStoreyId,
  onStoreyChange,
  units,
  onUnitsChange,
  saveState,
  version,
  pendingCount,
  onSaveRecover,
  canUndo = false,
  canRedo = false,
  onUndo,
  onRedo,
  copilotOpen = false,
  onCopilotToggle,
  onShare,
  onGenerate,
  generateLabel = 'Generate plans',
  generateBusy = false,
  generateDisabledReason,
  onBack,
  className,
}: TopBarProps): JSX.Element {
  const [editingName, setEditingName] = useState(false);
  const [draft, setDraft] = useState(projectName);
  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!editingName) setDraft(projectName);
  }, [projectName, editingName]);

  useEffect(() => {
    if (editingName) nameRef.current?.select();
  }, [editingName]);

  const commitName = (): void => {
    setEditingName(false);
    const next = draft.trim();
    if (next !== '' && next !== projectName) onRename?.(next);
    else setDraft(projectName);
  };

  return (
    <header
      className={cn(
        'flex h-topbar w-full shrink-0 items-center gap-3 border-b border-line bg-surface px-3',
        className,
      )}
    >
      {onBack === undefined ? null : (
        <IconButton label="Back to your projects" icon="chevron-left" size="sm" onClick={onBack} />
      )}

      {/* Project identity */}
      <div className="flex min-w-0 shrink items-center gap-2">
        {editingName ? (
          <input
            ref={nameRef}
            value={draft}
            aria-label="Project name"
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commitName}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                commitName();
              } else if (e.key === 'Escape') {
                e.preventDefault();
                setDraft(projectName);
                setEditingName(false);
              }
            }}
            className="garh-focus-ring h-8 w-56 rounded-md border border-line-strong bg-surface px-2 text-sm font-semibold text-ink"
          />
        ) : (
          <div className="min-w-0">
            {onRename === undefined ? (
              <h1 className="truncate text-sm font-semibold text-ink">{projectName}</h1>
            ) : (
              <button
                type="button"
                onClick={() => setEditingName(true)}
                className="garh-focus-ring max-w-[16rem] truncate rounded-sm text-sm font-semibold text-ink hover:underline"
                title="Rename this project"
              >
                {projectName}
              </button>
            )}
            {subtitle === undefined ? null : (
              <p className="truncate text-2xs text-ink-subtle garh-nums">{subtitle}</p>
            )}
          </div>
        )}
        {isDemo ? <Badge tone="brand">Demo</Badge> : null}
      </div>

      {/* Storey tabs */}
      {storeys !== undefined && storeys.length > 0 ? (
        <div
          role="radiogroup"
          aria-label="Storey"
          className="ml-1 flex shrink-0 items-center rounded-md bg-surface-muted p-0.5"
        >
          {storeys.map((storey) => {
            const active = storey.id === activeStoreyId;
            const button = (
              <button
                type="button"
                role="radio"
                aria-checked={active}
                disabled={onStoreyChange === undefined}
                onClick={() => onStoreyChange?.(storey.id)}
                className={cn(
                  'garh-focus-ring h-7 rounded px-2.5 text-xs font-medium transition-colors',
                  active ? 'bg-surface text-ink shadow-sm' : 'text-ink-muted hover:text-ink',
                )}
              >
                {storey.label}
              </button>
            );
            return storey.shortcut === undefined ? (
              <span key={storey.id}>{button}</span>
            ) : (
              <Tooltip
                key={storey.id}
                delayMs={400}
                content={<ShortcutHint label={storey.label} keys={storey.shortcut} />}
              >
                {button}
              </Tooltip>
            );
          })}
        </div>
      ) : null}

      {/* Undo / redo */}
      {onUndo !== undefined || onRedo !== undefined ? (
        <div className="flex shrink-0 items-center">
          <Tooltip delayMs={400} content={<ShortcutHint label="Undo" keys="⌘Z" />}>
            <IconButton
              label="Undo"
              icon="undo"
              size="sm"
              disabled={!canUndo || onUndo === undefined}
              onClick={onUndo}
            />
          </Tooltip>
          <Tooltip delayMs={400} content={<ShortcutHint label="Redo" keys="⌘⇧Z" />}>
            <IconButton
              label="Redo"
              icon="redo"
              size="sm"
              disabled={!canRedo || onRedo === undefined}
              onClick={onRedo}
            />
          </Tooltip>
        </div>
      ) : null}

      <AutosaveBadge
        state={saveState}
        version={version}
        pendingCount={pendingCount}
        onRecover={onSaveRecover}
        className="shrink-0"
      />

      <div className="ml-auto flex shrink-0 items-center gap-2">
        <UnitsToggle value={units} onChange={onUnitsChange} />

        {onCopilotToggle === undefined ? null : (
          <Tooltip
            delayMs={400}
            content={<ShortcutHint label={copilotOpen ? 'Hide the copilot' : 'Ask the copilot'} keys="/" />}
          >
            {/* `pressed` (→ aria-pressed) rather than two labels: a screen
                reader should hear one control whose state changed, not two
                buttons that swap places. */}
            <IconButton
              label="Copilot"
              icon="sparkles"
              size="sm"
              pressed={copilotOpen}
              onClick={onCopilotToggle}
            />
          </Tooltip>
        )}

        {onShare === undefined ? null : (
          <Button variant="secondary" size="sm" iconLeft="share" onClick={onShare}>
            Share
          </Button>
        )}

        {onGenerate === undefined ? null : generateDisabledReason !== undefined ? (
          <Tooltip content={generateDisabledReason}>
            <Button variant="primary" size="sm" iconLeft="sparkles" disabled>
              {generateLabel}
            </Button>
          </Tooltip>
        ) : (
          <Button
            variant="primary"
            size="sm"
            iconLeft="sparkles"
            loading={generateBusy}
            loadingLabel="Generating plan options"
            onClick={onGenerate}
          >
            {generateLabel}
          </Button>
        )}
      </div>
    </header>
  );
}
