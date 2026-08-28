/**
 * ViewsPanel — save where you are, and get back to it.
 *
 * An architect moves between "the kitchen detail", "the whole plan" and "the
 * street elevation" all day. Every one of those jumps is currently a manual
 * pan-and-zoom, and there is no way back to exactly where you were. This panel
 * is the shortest honest fix: name a camera, click it later, land on it.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THIS COMPONENT HOLDS NO POLICY
 * ════════════════════════════════════════════════════════════════════════════
 * What "fit selection" means, whether a view can be saved, what happens when a
 * 2D view is restored while the user is in 3D — all of it lives below
 * `useViews`, in modules that are testable without a DOM. The panel renders
 * what the controller says and calls what the controller offers. Its own
 * `ViewsPanel.test.tsx` therefore asserts against the VIEWPORT CONTROLLER after
 * a real click, not against a store flag: a panel wired to a store nothing
 * reads would pass a "the button got aria-pressed" test and fail that one.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY DELETE IS TWO CLICKS AND NOT A DIALOG
 * ════════════════════════════════════════════════════════════════════════════
 * A saved view costs a moment to lose and a moment to rebuild, so a modal with
 * a focus trap is more ceremony than the risk deserves. But a single-click
 * delete next to a single-click restore, on rows the user is clicking quickly,
 * is a mis-click that silently destroys something they named. Arming the button
 * ("Delete?") costs one extra click on the rare path and none on the common
 * one, and it never steals focus.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ACCESSIBILITY (§15)
 * ════════════════════════════════════════════════════════════════════════════
 * Every control is a real `<button>` whose `aria-label` names the view AND the
 * action ("Restore Kitchen detail", "Move Kitchen detail up"). The refusal
 * messages are in a `role="status"` region, so a screen-reader user is told the
 * list is full rather than watching a button do nothing.
 */

import { useCallback, useEffect, useRef, useState, type FormEvent, type JSX } from 'react';

import { cn, Icon } from '@garh/ui';

import type { CanvasCore } from '../canvas/core/context';
import { describeCamera } from './camera';
import type { RestoreOptions } from './restore';
import type { SaveRefusal } from './store';
import type { BuiltInViewSpec, NamedView } from './types';
import { useViews } from './useViews';

export interface ViewsPanelProps {
  readonly projectId: string;
  /** The canvas core from `CanvasRoot`'s `onCoreReady`. Null until it mounts. */
  readonly core: CanvasCore | null;
  readonly className?: string | undefined;
  /** Test seam, forwarded to `restoreCamera`. Never set in the app. */
  readonly restoreOptions?: RestoreOptions | undefined;
}

const REFUSAL_TEXT: Readonly<Record<SaveRefusal, string>> = {
  full: 'That is as many views as one project can keep. Delete one first.',
  'unusable-camera': 'That camera cannot be stored — nothing was saved.',
  'no-canvas': 'The drawing is still loading. Try again in a moment.',
};

const CONTROL =
  'garh-focus-ring inline-flex h-7 w-7 shrink-0 items-center justify-center rounded ' +
  'text-ink-muted transition-colors hover:bg-surface-muted hover:text-ink ' +
  'disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent';

function ArrowGlyph({ up }: { up: boolean }): JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      width={14}
      height={14}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={true}
      focusable="false"
    >
      <path d={up ? 'M12 19 L12 5 M6 11 L12 5 L18 11' : 'M12 5 L12 19 M6 13 L12 19 L18 13'} />
    </svg>
  );
}

interface RowProps {
  readonly view: NamedView;
  readonly index: number;
  readonly count: number;
  readonly disabled: boolean;
  readonly armedDelete: boolean;
  readonly onRestore: () => void;
  readonly onRename: (name: string) => void;
  readonly onMove: (toIndex: number) => void;
  readonly onArmDelete: () => void;
  readonly onDelete: () => void;
}

function ViewRow({
  view,
  index,
  count,
  disabled,
  armedDelete,
  onRestore,
  onRename,
  onMove,
  onArmDelete,
  onDelete,
}: RowProps): JSX.Element {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(view.name);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  const commit = useCallback(() => {
    setEditing(false);
    onRename(draft);
  }, [draft, onRename]);

  return (
    <li className="flex items-center gap-1 rounded px-1 py-0.5 hover:bg-surface-muted">
      {editing ? (
        <input
          ref={inputRef}
          className="min-w-0 flex-1 rounded border border-line bg-surface px-1 py-0.5 text-xs text-ink"
          value={draft}
          aria-label={`Rename ${view.name}`}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={commit}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              commit();
            } else if (event.key === 'Escape') {
              event.preventDefault();
              setDraft(view.name);
              setEditing(false);
            }
          }}
        />
      ) : (
        <button
          type="button"
          className="garh-focus-ring min-w-0 flex-1 rounded px-1 py-1 text-left disabled:cursor-not-allowed disabled:opacity-40"
          disabled={disabled}
          aria-label={`Restore ${view.name}`}
          title={describeCamera(view.camera)}
          onClick={onRestore}
        >
          <span className="block truncate text-xs leading-4 text-ink">{view.name}</span>
        </button>
      )}

      <span
        className="shrink-0 rounded bg-surface-muted px-1 text-2xs font-medium uppercase text-ink-muted"
        aria-hidden={true}
      >
        {view.camera.mode}
      </span>

      <button
        type="button"
        className={CONTROL}
        aria-label={`Rename ${view.name}`}
        onClick={() => {
          setDraft(view.name);
          setEditing(true);
        }}
      >
        <Icon name="edit" size={14} />
      </button>

      <button
        type="button"
        className={CONTROL}
        disabled={index === 0}
        aria-label={`Move ${view.name} up`}
        onClick={() => onMove(index - 1)}
      >
        <ArrowGlyph up={true} />
      </button>

      <button
        type="button"
        className={CONTROL}
        disabled={index === count - 1}
        aria-label={`Move ${view.name} down`}
        onClick={() => onMove(index + 1)}
      >
        <ArrowGlyph up={false} />
      </button>

      <button
        type="button"
        className={cn(CONTROL, armedDelete ? 'w-auto px-1 text-fail-ink' : null)}
        aria-label={armedDelete ? `Confirm delete ${view.name}` : `Delete ${view.name}`}
        onClick={armedDelete ? onDelete : onArmDelete}
      >
        {armedDelete ? (
          <span className="text-2xs font-medium">Delete?</span>
        ) : (
          <Icon name="trash" size={14} />
        )}
      </button>
    </li>
  );
}

function BuiltInButton({
  spec,
  disabled,
  onClick,
}: {
  spec: BuiltInViewSpec;
  disabled: boolean;
  onClick: () => void;
}): JSX.Element {
  const unavailable = spec.extent === null;
  return (
    <button
      type="button"
      className={cn(
        'garh-focus-ring rounded border border-line px-2 py-1 text-2xs text-ink-muted',
        'transition-colors hover:bg-surface-muted hover:text-ink',
        'disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent',
      )}
      disabled={disabled || unavailable}
      // The reason is on the control itself, so hovering a greyed-out button
      // answers "why" instead of leaving the user to guess.
      title={spec.reason ?? spec.label}
      onClick={onClick}
    >
      {spec.label}
    </button>
  );
}

export function ViewsPanel({
  projectId,
  core,
  className,
  restoreOptions,
}: ViewsPanelProps): JSX.Element {
  const views = useViews({ projectId, core, restoreOptions });
  const [name, setName] = useState('');
  const [refused, setRefused] = useState<SaveRefusal | null>(null);
  const [armedDeleteId, setArmedDeleteId] = useState<string | null>(null);

  const save = useCallback(
    (event: FormEvent) => {
      event.preventDefault();
      const result = views.saveCurrent(name);
      setRefused(result.refused);
      if (result.view !== null) setName('');
    },
    [name, views],
  );

  // Any other action disarms a primed delete: an armed row left sitting there
  // while the user does three other things is a trap waiting to be sprung.
  const disarm = useCallback(() => setArmedDeleteId(null), []);

  return (
    <section
      className={cn(
        'pointer-events-auto flex w-72 flex-col gap-2 rounded-md border border-line bg-surface/95 p-3 shadow-sm backdrop-blur',
        className,
      )}
      aria-label="Saved views"
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-xs font-semibold text-ink">Views</h3>
        <span className="text-2xs text-ink-muted garh-nums">{views.views.length}</span>
      </div>

      <div className="flex flex-wrap gap-1">
        {views.builtIns.map((spec) => (
          <BuiltInButton
            key={spec.id}
            spec={spec}
            disabled={!views.ready}
            onClick={() => {
              disarm();
              views.restoreBuiltIn(spec.id);
            }}
          />
        ))}
      </div>

      <form className="flex items-center gap-1" onSubmit={save}>
        <input
          className="min-w-0 flex-1 rounded border border-line bg-surface px-2 py-1 text-xs text-ink placeholder:text-ink-subtle"
          value={name}
          placeholder={views.suggestedName}
          aria-label="Name for the current view"
          onChange={(event) => setName(event.target.value)}
        />
        <button
          type="submit"
          className="garh-focus-ring rounded border border-line px-2 py-1 text-2xs font-medium text-ink transition-colors hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-40"
          disabled={!views.ready}
          aria-label="Save the current view"
        >
          Save view
        </button>
      </form>

      {views.views.length === 0 ? (
        <p className="text-2xs leading-4 text-ink-muted">
          Frame something, name it, and it will be one click away — in the plan or in 3D.
        </p>
      ) : (
        <ul className="flex flex-col">
          {views.views.map((view, index) => (
            <ViewRow
              key={view.id}
              view={view}
              index={index}
              count={views.views.length}
              disabled={!views.ready}
              armedDelete={armedDeleteId === view.id}
              onRestore={() => {
                disarm();
                views.restore(view.id);
              }}
              onRename={(next) => {
                disarm();
                views.rename(view.id, next);
              }}
              onMove={(toIndex) => {
                disarm();
                views.move(view.id, toIndex);
              }}
              onArmDelete={() => setArmedDeleteId(view.id)}
              onDelete={() => {
                setArmedDeleteId(null);
                views.remove(view.id);
              }}
            />
          ))}
        </ul>
      )}

      {/* Live region: a refusal must be announced, not just coloured. */}
      <p className="text-2xs leading-4 text-fail-ink empty:hidden" role="status">
        {refused === null ? '' : REFUSAL_TEXT[refused]}
      </p>
    </section>
  );
}
