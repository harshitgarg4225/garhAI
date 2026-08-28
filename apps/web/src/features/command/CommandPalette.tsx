/**
 * CommandPalette — ⌘K / Ctrl-K, fuzzy search over everything the app can do.
 *
 * An architect coming from AutoCAD or Revit navigates by keyboard and reaches
 * for a command line before a menu. A palette is that habit, and it is also the
 * only surface in the product where the whole command set is visible at once —
 * which is why it reads the registry rather than a list of its own.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ONE HOLE THIS CLOSES ON PURPOSE
 * ════════════════════════════════════════════════════════════════════════════
 * The root carries `data-garh-keys="off"`, the opt-out `lib/keymap.ts` already
 * honours in `isTypingTarget`. While the search input has focus the guard
 * catches it anyway (it is an `<input>`), but the moment you click a row focus
 * lands on the list — and without the attribute, typing `w` over an open
 * palette would arm the wall tool on the drawing behind it. One attribute, the
 * app's own mechanism, no second opinion about when the keyboard is busy.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * DISABLED ROWS ARE SHOWN, NOT HIDDEN
 * ════════════════════════════════════════════════════════════════════════════
 * Undo with an empty history, or a tool while the 3D view is up, renders greyed
 * with `aria-disabled` and refuses to run. Filtering it out instead would make
 * the palette look broken — you search "undo", nothing appears, and you cannot
 * tell whether the app lacks the command or the command lacks a target. The
 * refusal is the registry's, not this component's: `run()` returns `'disabled'`
 * and the palette simply stays open.
 *
 * `aria-disabled` is a PICTURE of `enabled()`, taken when the row was drawn.
 * This component cannot subscribe to the stores a command's predicate happens
 * to read — it is generic over the registry, and a command may read anything —
 * so a row that becomes available while the palette is open (a collaborator's
 * op arriving over SSE and enabling Undo) re-draws on the next render rather
 * than instantly. Every keystroke in the search box is such a render, which
 * bounds the staleness to one character, and `registry.run()` re-checks the
 * predicate at the moment of invocation regardless of what the row shows. The
 * displayed state can lag; the refusal cannot.
 */

import { useCallback, useEffect, useId, useMemo, useRef } from 'react';
import { createPortal } from 'react-dom';

import { Icon, cn } from '@garh/ui';

import { formatBinding, isMacPlatform } from './binding';
import { useCommands } from './hooks';
import { commandRegistry, type CommandRegistry } from './registry';
import {
  flattenGroups,
  groupMatches,
  normaliseQuery,
  searchCommands,
  type CommandMatch,
  type MatchRange,
} from './search';
import { useCommandUiStore } from './store';

export interface CommandPaletteProps {
  /** Defaults to the app registry. Tests pass their own. */
  readonly registry?: CommandRegistry | undefined;
  /** Override platform detection for the key hints. Tests only. */
  readonly mac?: boolean | undefined;
}

/** Title with the matched characters emphasised. Ranges are half-open. */
function Highlighted({
  text,
  ranges,
}: {
  text: string;
  ranges: readonly MatchRange[];
}): JSX.Element {
  if (ranges.length === 0) return <>{text}</>;
  const pieces: JSX.Element[] = [];
  let cursor = 0;
  ranges.forEach(([start, end], index) => {
    if (start > cursor) pieces.push(<span key={`p${index}`}>{text.slice(cursor, start)}</span>);
    pieces.push(
      <mark key={`m${index}`} className="bg-transparent font-semibold text-brand-ink">
        {text.slice(start, end)}
      </mark>,
    );
    cursor = end;
  });
  if (cursor < text.length) pieces.push(<span key="tail">{text.slice(cursor)}</span>);
  return <>{pieces}</>;
}

export function CommandPalette({ registry, mac }: CommandPaletteProps = {}): JSX.Element | null {
  const active = registry ?? commandRegistry;
  const isMac = mac ?? isMacPlatform();

  const open = useCommandUiStore((s) => s.paletteOpen);
  const query = useCommandUiStore((s) => s.query);
  const highlightedId = useCommandUiStore((s) => s.highlightedId);

  const commands = useCommands(active);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const baseId = useId();
  const listId = `${baseId}-list`;

  const runnable = useMemo(
    () => commands.filter((command) => command.run !== null && command.hidden !== true),
    [commands],
  );
  const matches = useMemo(() => searchCommands(runnable, query), [runnable, query]);
  const groups = useMemo(
    () => groupMatches(matches, normaliseQuery(query) !== ''),
    [matches, query],
  );
  const flat = useMemo(() => flattenGroups(groups), [groups]);

  /**
   * The row Enter would run. The stored id wins while it is still in the
   * results; otherwise the first row does — see `store.ts` on why the cursor is
   * an id and not an index.
   */
  const currentId = useMemo(() => {
    if (highlightedId !== null && flat.some((m) => m.command.id === highlightedId)) {
      return highlightedId;
    }
    return flat[0]?.command.id ?? null;
  }, [flat, highlightedId]);

  const invoke = useCallback(
    (id: string) => {
      // The registry re-checks `enabled` here; the greyed-out styling below is
      // only a picture of that check, and the picture is one render old.
      if (active.run(id, 'palette') !== 'ran') return;
      useCommandUiStore.getState().closePalette();
    },
    [active],
  );

  const move = useCallback(
    (delta: number) => {
      if (flat.length === 0) return;
      const index = flat.findIndex((m) => m.command.id === currentId);
      const next = ((((index < 0 ? 0 : index) + delta) % flat.length) + flat.length) % flat.length;
      const target = flat[next];
      if (target !== undefined) useCommandUiStore.getState().setHighlighted(target.command.id);
    },
    [flat, currentId],
  );

  // Focus the input on open. Without this the palette opens and the first
  // keystroke goes to whatever was focused behind it.
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  // Keep the cursor on screen while arrowing through a long list.
  // `scrollIntoView` is not implemented in jsdom, so it is called defensively —
  // the palette must not throw in the test environment it is verified in.
  useEffect(() => {
    if (!open || currentId === null) return;
    const node = listRef.current?.querySelector<HTMLElement>(`[data-command-id="${currentId}"]`);
    node?.scrollIntoView?.({ block: 'nearest' });
  }, [open, currentId]);

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLInputElement>) => {
      const store = useCommandUiStore.getState();
      // ⌘K closes what ⌘K opened. The global listener cannot do it: focus is in
      // a text input, and `isTypingTarget` — correctly — refuses to fire there.
      if ((isMac ? event.metaKey : event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        store.closePalette();
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        store.closePalette();
        return;
      }
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        move(1);
        return;
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        move(-1);
        return;
      }
      if (event.key === 'Home' && flat[0] !== undefined) {
        event.preventDefault();
        store.setHighlighted(flat[0].command.id);
        return;
      }
      if (event.key === 'End' && flat.length > 0) {
        event.preventDefault();
        const last = flat[flat.length - 1];
        if (last !== undefined) store.setHighlighted(last.command.id);
        return;
      }
      if (event.key === 'Enter' && currentId !== null) {
        event.preventDefault();
        invoke(currentId);
      }
    },
    [currentId, flat, invoke, isMac, move],
  );

  if (!open) return null;
  if (typeof document === 'undefined') return null;

  return createPortal(
    <div
      className="fixed inset-0 z-dialog flex items-start justify-center p-4 pt-[10vh]"
      data-garh-keys="off"
    >
      <div
        className="absolute inset-0 animate-fade-in bg-scrim/55 backdrop-blur-[1px]"
        aria-hidden="true"
        onClick={() => useCommandUiStore.getState().closePalette()}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className={cn(
          'relative flex max-h-[70vh] w-full max-w-xl flex-col overflow-hidden',
          'animate-pop-in rounded-lg border border-line bg-surface shadow-lg',
        )}
      >
        <div className="flex items-center gap-2 border-b border-line px-3">
          <Icon name="search" size={16} className="shrink-0 text-ink-subtle" aria-hidden={true} />
          <input
            ref={inputRef}
            type="text"
            role="combobox"
            aria-expanded={true}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-label="Search commands"
            aria-activedescendant={currentId === null ? undefined : `${baseId}-${currentId}`}
            placeholder="Type a command…"
            value={query}
            onChange={(event) => useCommandUiStore.getState().setQuery(event.target.value)}
            onKeyDown={onKeyDown}
            className="h-11 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-ink-subtle"
          />
          <kbd className="rounded border border-line px-1.5 py-0.5 text-2xs text-ink-subtle">
            Esc
          </kbd>
        </div>

        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          aria-label="Commands"
          className="min-h-0 flex-1 overflow-y-auto py-1"
        >
          {flat.length === 0 ? (
            <li className="px-4 py-6 text-center text-sm text-ink-muted" role="presentation">
              Nothing matches “{query}”.
            </li>
          ) : (
            groups.map((group) => (
              <li key={group.group} role="presentation">
                <p className="px-3 pb-1 pt-2 text-2xs font-semibold uppercase tracking-wide text-ink-subtle">
                  {group.group}
                </p>
                <ul role="group" aria-label={group.group}>
                  {group.matches.map((match) => (
                    <Row
                      key={match.command.id}
                      match={match}
                      idPrefix={baseId}
                      current={match.command.id === currentId}
                      enabled={active.isEnabled(match.command.id)}
                      keys={active
                        .bindingsOf(match.command.id)
                        .map((binding) => formatBinding(binding, isMac))}
                      onRun={invoke}
                    />
                  ))}
                </ul>
              </li>
            ))
          )}
        </ul>
      </div>
    </div>,
    document.body,
  );
}

function Row({
  match,
  idPrefix,
  current,
  enabled,
  keys,
  onRun,
}: {
  match: CommandMatch;
  idPrefix: string;
  current: boolean;
  enabled: boolean;
  keys: readonly string[];
  onRun: (id: string) => void;
}): JSX.Element {
  const { command } = match;
  return (
    <li
      id={`${idPrefix}-${command.id}`}
      data-command-id={command.id}
      role="option"
      aria-selected={current}
      aria-disabled={!enabled}
      onClick={() => onRun(command.id)}
      onMouseMove={() => useCommandUiStore.getState().setHighlighted(command.id)}
      className={cn(
        'flex cursor-pointer items-center gap-2.5 px-3 py-1.5 text-sm',
        current ? 'bg-brand-soft text-brand-ink' : 'text-ink',
        enabled ? null : 'cursor-default opacity-45',
      )}
    >
      {command.icon === undefined ? (
        <span className="h-4 w-4 shrink-0" aria-hidden={true} />
      ) : (
        <Icon name={command.icon} size={16} className="shrink-0" aria-hidden={true} />
      )}
      <span className="min-w-0 flex-1 truncate">
        <Highlighted text={command.title} ranges={match.ranges} />
      </span>
      {keys.map((key) => (
        <kbd
          key={key}
          className="shrink-0 rounded border border-line bg-surface-muted px-1.5 py-0.5 text-2xs text-ink-muted"
        >
          {key}
        </kbd>
      ))}
    </li>
  );
}
