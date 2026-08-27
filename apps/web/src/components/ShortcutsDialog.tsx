/**
 * ShortcutsDialog — the keyboard map, rendered from the keyboard map.
 *
 * §15 asks for full keyboard operability and for the shortcuts to be
 * discoverable; §12 puts every binding in one array. This reads that array, so
 * a binding added in `lib/keymap.ts` shows up here with no second edit, and a
 * sheet that disagrees with the handler is structurally impossible.
 *
 * Presentational, like everything else in this folder: it takes `open` and
 * `onOpenChange` and nothing else. The page that owns the `?` shortcut owns the
 * state.
 */

import { Dialog, cn } from '@garh/ui';

import { KEY_BINDINGS, formatShortcut, type CommandId, type KeyBinding } from '../lib/keymap';

/**
 * Section order and titles. A command not listed here still renders, under
 * "More" — a new binding should never be invisible just because nobody
 * remembered to file it.
 */
const SECTIONS: readonly { title: string; commands: readonly CommandId[] }[] = [
  {
    title: 'Tools',
    commands: [
      'tool.select',
      'tool.wall',
      'tool.door',
      'tool.window',
      'tool.stair',
      'tool.balcony',
      'tool.measure',
      'tool.furniture',
    ],
  },
  {
    title: 'While you are drawing',
    commands: ['tool.commit', 'tool.cancel'],
  },
  {
    title: 'Editing',
    commands: ['edit.undo', 'edit.redo', 'edit.delete', 'edit.selectAll'],
  },
  {
    // Filed explicitly rather than left to fall into "More": `/` is the one
    // shortcut a new user is most likely to want and least likely to guess,
    // and it sits next to Editing because that is what it does — the copilot's
    // Apply goes through the same op group and the same single undo.
    title: 'Asking for a change',
    commands: ['copilot.focus'],
  },
  {
    title: 'Getting around',
    commands: [
      'storey.1',
      'storey.2',
      'storey.3',
      'view.toggle',
      'view.fit',
      'view.zoomIn',
      'view.zoomOut',
    ],
  },
  {
    title: 'What you can see',
    commands: ['snap.toggle', 'view.grid', 'view.dimensions', 'help.shortcuts'],
  },
];

/** One row per command, with every key that triggers it. */
function rowsFor(
  commands: readonly CommandId[],
): { command: CommandId; binding: KeyBinding; keys: string[] }[] {
  const rows: { command: CommandId; binding: KeyBinding; keys: string[] }[] = [];
  for (const command of commands) {
    const matches = KEY_BINDINGS.filter((b) => b.command === command);
    const first = matches[0];
    if (first === undefined) continue;
    // Deduped: `?` is registered twice (shifted and not) so it works on every
    // layout, and printing it twice would look like a mistake.
    const keys = [...new Set(matches.map((b) => formatShortcut(b)))];
    rows.push({ command, binding: first, keys });
  }
  return rows;
}

export interface ShortcutsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ShortcutsDialog({ open, onOpenChange }: ShortcutsDialogProps): JSX.Element {
  const listed = new Set(SECTIONS.flatMap((s) => s.commands));
  const leftovers = [...new Set(KEY_BINDINGS.map((b) => b.command))].filter((c) => !listed.has(c));
  const sections =
    leftovers.length === 0 ? SECTIONS : [...SECTIONS, { title: 'More', commands: leftovers }];

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Keyboard shortcuts"
      description="Every one of these has a button too — nothing here is keyboard-only."
      size="lg"
    >
      <div className="grid gap-5 sm:grid-cols-2">
        {sections.map((section) => (
          <section key={section.title}>
            <h3 className="mb-2 text-2xs font-semibold uppercase tracking-wide text-ink-subtle">
              {section.title}
            </h3>
            <dl className="space-y-1.5">
              {rowsFor(section.commands).map(({ command, binding, keys }) => (
                <div key={command} className="flex items-baseline justify-between gap-3">
                  <dt className="min-w-0 text-xs text-ink-muted">{binding.description}</dt>
                  <dd className="flex shrink-0 gap-1">
                    {keys.map((key) => (
                      <kbd
                        key={key}
                        className={cn(
                          'rounded border border-line-strong bg-surface-muted px-1.5 py-0.5',
                          'text-2xs font-medium text-ink garh-nums',
                        )}
                      >
                        {key}
                      </kbd>
                    ))}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
      <p className="mt-5 text-2xs leading-4 text-ink-subtle">
        While you are drawing, typing a number sets the exact length — the keys above go back to
        being shortcuts as soon as you finish or press Esc.
      </p>
    </Dialog>
  );
}
