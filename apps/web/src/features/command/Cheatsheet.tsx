/**
 * Cheatsheet — every key the app answers to, grouped, in one sheet.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY THIS EXISTS ALONGSIDE `components/ShortcutsDialog`
 * ════════════════════════════════════════════════════════════════════════════
 * That dialog renders `KEY_BINDINGS` and is right to: for the fixed map it is
 * the same single source this feature mirrors. What it cannot do is show a
 * binding that is not in `KEY_BINDINGS`, because `CommandId` there is a closed
 * union — so ⌘K, the palette's own key, is structurally invisible to it, and so
 * is every key a feature registers at runtime from now on.
 *
 * This sheet reads the REGISTRY, which contains both: the whole fixed map,
 * folded in by `defaultCommands.ts`, plus everything registered since. It is
 * mounted at the app root rather than on one tab, so `⌘/` answers on the
 * dashboard and the sheets tab too — the old dialog only ever existed on Plan.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE PANEL IS HAND-ROLLED RATHER THAN `<Dialog>`
 * ════════════════════════════════════════════════════════════════════════════
 * Only because of one attribute. `data-garh-keys="off"` has to sit on the
 * OUTERMOST node — it is the app's documented "the keyboard is busy here"
 * opt-out, honoured by `isTypingTarget`, and it works by `closest()` from the
 * focused element. `<Dialog>` owns its own root, so the attribute could only go
 * on the children, leaving its close button outside the guard: with focus
 * there, `w` would arm the wall tool on the drawing behind the sheet. The focus
 * trap, Escape handling and scroll lock are `@garh/ui`'s own hooks, so nothing
 * else about the dialog behaviour is re-implemented here.
 */

import { useRef } from 'react';
import { createPortal } from 'react-dom';

import { Icon, cn, useBodyScrollLock, useFocusTrap, useOnEscape } from '@garh/ui';

import { formatBinding, isMacPlatform } from './binding';
import { useCommands } from './hooks';
import { commandRegistry, type CommandRegistry } from './registry';
import { useCommandUiStore } from './store';
import { COMMAND_GROUPS, type Command, type CommandGroup } from './types';

export interface CheatsheetProps {
  readonly registry?: CommandRegistry | undefined;
  readonly mac?: boolean | undefined;
}

export function Cheatsheet({ registry, mac }: CheatsheetProps = {}): JSX.Element | null {
  const active = registry ?? commandRegistry;
  const isMac = mac ?? isMacPlatform();
  const open = useCommandUiStore((s) => s.cheatsheetOpen);
  const commands = useCommands(active);
  const panelRef = useRef<HTMLDivElement>(null);

  const close = (): void => useCommandUiStore.getState().closeCheatsheet();
  useFocusTrap(panelRef, open);
  useOnEscape(open, close);
  useBodyScrollLock(open);

  if (!open) return null;
  if (typeof document === 'undefined') return null;

  // Every command that has a key, including the ones this registry only
  // documents — `run: null` means the action lives elsewhere, not that the key
  // is fictional, and a cheatsheet that omitted them would be wrong.
  const bound = commands.filter((command) => active.bindingsOf(command.id).length > 0);
  const sections = COMMAND_GROUPS.map((group) => ({
    group,
    rows: bound.filter((command) => command.group === group),
  })).filter((section) => section.rows.length > 0);

  // The palette's key, read back out of the registry rather than written into
  // this sentence: a hard-coded "⌘K" here would keep saying ⌘K after someone
  // rebound the palette, and a sheet whose own header is stale is worse than no
  // header. Absent (nobody registered the palette) means no sentence at all.
  const paletteKey = active.bindingsOf('palette.open')[0];

  return createPortal(
    <div
      className="fixed inset-0 z-dialog flex items-end justify-center p-0 sm:items-center sm:p-6"
      data-garh-keys="off"
    >
      <div
        className="absolute inset-0 animate-fade-in bg-scrim/55 backdrop-blur-[1px]"
        aria-hidden="true"
        onClick={close}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="Keyboard shortcuts"
        className={cn(
          'relative flex max-h-[92vh] w-full max-w-2xl flex-col overflow-hidden',
          'animate-pop-in rounded-t-xl border border-line bg-surface shadow-lg sm:rounded-xl',
        )}
      >
        <div className="flex items-start gap-4 border-b border-line px-5 py-4">
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold leading-6 text-ink">Keyboard shortcuts</h2>
            <p className="mt-1 text-sm leading-5 text-ink-muted">
              Everything the app answers to.
              {paletteKey === undefined
                ? null
                : ` Press ${formatBinding(paletteKey, isMac)} to search commands by name instead.`}
            </p>
          </div>
          <button
            type="button"
            aria-label="Close"
            onClick={close}
            className="garh-focus-ring -mr-1 -mt-1 inline-flex h-8 w-8 items-center justify-center rounded text-ink-muted hover:bg-surface-muted"
          >
            <Icon name="x" size={16} aria-hidden={true} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <div className="grid gap-6 sm:grid-cols-2">
            {sections.map((section) => (
              <Section
                key={section.group}
                group={section.group}
                rows={section.rows}
                keysOf={(command) =>
                  active.bindingsOf(command.id).map((binding) => formatBinding(binding, isMac))
                }
              />
            ))}
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function Section({
  group,
  rows,
  keysOf,
}: {
  group: CommandGroup;
  rows: readonly Command[];
  keysOf: (command: Command) => readonly string[];
}): JSX.Element {
  return (
    <section aria-label={group}>
      <h3 className="mb-2 text-2xs font-semibold uppercase tracking-wide text-ink-subtle">
        {group}
      </h3>
      <dl className="space-y-1">
        {rows.map((command) => (
          <div key={command.id} className="flex items-baseline gap-3">
            <dt className="min-w-0 flex-1 text-sm text-ink">{command.title}</dt>
            <dd className="flex shrink-0 gap-1">
              {keysOf(command).map((key) => (
                <kbd
                  key={key}
                  className="rounded border border-line bg-surface-muted px-1.5 py-0.5 text-2xs text-ink-muted"
                >
                  {key}
                </kbd>
              ))}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
