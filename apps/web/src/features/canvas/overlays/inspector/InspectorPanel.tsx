/**
 * InspectorPanel.tsx — the right panel (§12).
 *
 * A thin renderer over `fields.ts`. Every decision about what is editable, what
 * a multi-select shares and which op an edit becomes lives there, where it is
 * tested without a DOM; this file's whole job is to pick the right control and
 * dispatch the ops the field hands back.
 *
 * THE CONTROLS
 *   length   `LengthInput` from @garh/ui — THE mm boundary. It parses 12'6",
 *            3.8m, 380cm and 3800, shows the exact millimetre value as a hint,
 *            commits on blur and Enter, reverts on Escape. Never re-implemented
 *            here: a second parser is a second set of rounding rules.
 *   area     a text input through `parseAreaInput`, which understands sq ft,
 *            m², gaj and `12x14`.
 *   enum     a `Select`.
 *   toggle   a checkbox.
 *   readonly a disabled row that says WHY, rather than a property that vanished.
 *
 * ONE OP GROUP PER EDIT. `field.build(next)` returns every op the change needs
 * across the whole selection, and they are dispatched together — five walls
 * thickened at once is one undo step, not five.
 */

import { useCallback, useId } from 'react';

import { Button, Chip, Field, LengthInput, SelectField, cn } from '@garh/ui';

import type { HouseModel, Op, UnitsDisplay } from '@garh/model';

import { useModelStore } from '../../../../stores/model';
import { useUiStore } from '../../../../stores/ui';
import { areaEditSeed, areaHint, parseAreaInput } from '../format';
import { inspectorSelection, type InspectorField, type InspectorSelection } from './fields';

export interface InspectorPanelProps {
  house: HouseModel;
  /** Selected element ids, from the selection store. */
  selectedIds: readonly string[];
  display: UnitsDisplay;
  /** Overrides the derived selection — used by the specs and by Storybook. */
  selection?: InspectorSelection | undefined;
  className?: string | undefined;
}

export function InspectorPanel({
  house,
  selectedIds,
  display,
  selection,
  className,
}: InspectorPanelProps): JSX.Element {
  const resolved = selection ?? inspectorSelection(house, selectedIds, { display });

  const dispatch = useCallback((ops: readonly Op[], label: string): void => {
    if (ops.length === 0) return;
    const result = useModelStore.getState().dispatch(ops, { label, source: 'manual' });
    if (result.ok) return;
    // Golden rule 9: what happened, and what to do about it.
    useUiStore.getState().pushToast({
      tone: 'warning',
      title: result.issues[0]?.message ?? 'That change is not valid here.',
      // `?? null`: ToastInput.description is `string | null` and does not admit
      // an explicit undefined under exactOptionalPropertyTypes (TS2375).
      description: result.issues[0]?.fix ?? null,
      dedupeKey: 'inspector-rejected',
    });
  }, []);

  return (
    <aside
      className={cn('flex h-full w-full flex-col overflow-y-auto bg-surface', className)}
      aria-label="Properties"
    >
      <header className="sticky top-0 z-10 border-b border-line bg-surface px-3 py-2.5">
        <h2 className="truncate text-sm font-semibold text-ink">{resolved.title}</h2>
        {resolved.subtitle === null ? null : (
          <p className="truncate text-xs text-ink-muted garh-nums">{resolved.subtitle}</p>
        )}
      </header>

      {resolved.fields.length === 0 ? (
        <p className="px-3 py-6 text-xs text-ink-muted">
          {resolved.kind === 'none'
            ? 'Click something on the plan to see and change its properties.'
            : (resolved.subtitle ?? 'Nothing to edit here yet.')}
        </p>
      ) : (
        <div className="flex flex-col gap-3 px-3 py-3">
          {resolved.fields.map((field) => (
            <FieldRow key={field.key} field={field} display={display} onDispatch={dispatch} />
          ))}
        </div>
      )}

      {resolved.actions.length === 0 ? null : (
        <footer className="mt-auto flex flex-col gap-2 border-t border-line px-3 py-3">
          {resolved.actions.map((action) => (
            <Button
              key={action.key}
              variant={action.tone === 'danger' ? 'danger' : 'secondary'}
              size="sm"
              onClick={() => dispatch(action.ops, action.undoLabel)}
            >
              {action.label}
            </Button>
          ))}
        </footer>
      )}
    </aside>
  );
}

// ---------------------------------------------------------------------------
// One row
// ---------------------------------------------------------------------------

function FieldRow({
  field,
  display,
  onDispatch,
}: {
  field: InspectorField;
  display: UnitsDisplay;
  onDispatch: (ops: readonly Op[], label: string) => void;
}): JSX.Element {
  // For the toggle row's explicit label/hint wiring. Unconditional — hooks
  // must run on every render path, including the readonly early return.
  const toggleId = useId();

  const commit = (next: number | string | boolean): void => {
    onDispatch(field.build(next), field.undoLabel);
  };

  if (!field.editable || field.kind === 'readonly') {
    return (
      <div>
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-xs font-medium text-ink-muted">{field.label}</span>
          <span className="truncate text-xs text-ink garh-nums">{field.displayText}</span>
        </div>
        {field.reason === undefined ? null : (
          <p className="mt-0.5 text-2xs leading-tight text-ink-subtle">{field.reason}</p>
        )}
      </div>
    );
  }

  switch (field.kind) {
    case 'length':
      return (
        <LengthInput
          label={field.label}
          valueMm={typeof field.value === 'number' ? field.value : null}
          onCommitMm={(mm) => commit(mm)}
          display={display}
          // Thickness, sill and offset are millimetre-native fields — an
          // architect typing "115" into a wall-thickness box means 115 mm, not
          // 115 feet, whatever the project displays in.
          bareUnit={field.key === 'thickness' || field.key === 'sill' ? 'mm' : display}
          hint={field.mixed ? 'Mixed — typing sets them all' : field.hint}
          minMm={field.minMm}
          maxMm={field.maxMm}
        />
      );

    case 'area':
      return (
        <TextRow
          label={field.label}
          hint={field.hint ?? areaHint(display)}
          placeholder={field.mixed ? 'Mixed' : 'Not set'}
          initial={typeof field.value === 'number' ? areaEditSeed(field.value, display) : ''}
          onCommit={(raw) => {
            const parsed = parseAreaInput(raw, display);
            if (parsed.ok) commit(parsed.mm2);
          }}
        />
      );

    case 'count':
      return (
        <TextRow
          label={field.label}
          hint={field.hint}
          placeholder={field.mixed ? 'Mixed' : ''}
          initial={typeof field.value === 'number' ? String(field.value) : ''}
          onCommit={(raw) => {
            const n = Number.parseInt(raw.trim(), 10);
            if (Number.isFinite(n)) commit(n);
          }}
        />
      );

    case 'text':
      return (
        <TextRow
          label={field.label}
          hint={field.hint}
          placeholder={field.mixed ? 'Mixed' : ''}
          initial={typeof field.value === 'string' ? field.value : ''}
          onCommit={(raw) => commit(raw.trim())}
        />
      );

    // Enum and text rows go through the @garh/ui `Field` scaffold, the same
    // as `LengthInput`. An earlier version hand-rolled a wrapping `<label>`
    // with the hint (and, for selects, the option text) INSIDE it — which
    // made the hint part of the control's accessible name. That is exactly
    // the "re-typed and half-forgotten" wiring `Field`'s header warns about,
    // and it was found executed: plan-canvas.spec.ts's `getByLabel('Type')`
    // matched the NAME input too, because its hint says "…use the room type
    // as the label". Labels name, hints describe (`aria-describedby`).
    case 'enum':
      return (
        <SelectField
          label={field.label}
          hint={field.hint}
          value={typeof field.value === 'string' ? field.value : ''}
          onValueChange={(value) => commit(value)}
          options={(field.options ?? []).map((option) => ({
            value: option.value,
            label: option.label,
          }))}
          {...(field.mixed ? { placeholder: 'Mixed' } : {})}
        />
      );

    case 'toggle':
      return (
        <div className="flex items-start gap-2">
          <input
            id={toggleId}
            type="checkbox"
            className="garh-focus-ring mt-0.5 h-4 w-4 rounded-sm border-line-strong"
            checked={field.value === true}
            aria-describedby={field.hint === undefined ? undefined : `${toggleId}-hint`}
            // `indeterminate` is a DOM property, not an attribute, so React
            // cannot set it declaratively — the ref is the only way to show a
            // mixed selection honestly instead of picking one of the two.
            ref={(node) => {
              if (node !== null) node.indeterminate = field.mixed;
            }}
            onChange={(e) => commit(e.target.checked)}
          />
          <span>
            <label htmlFor={toggleId} className="block text-xs font-medium text-ink">
              {field.label}
            </label>
            {field.hint === undefined ? null : (
              <span
                id={`${toggleId}-hint`}
                className="block text-2xs leading-tight text-ink-subtle"
              >
                {field.hint}
              </span>
            )}
          </span>
          {field.mixed ? (
            <Chip severity="neutral" size="sm">
              Mixed
            </Chip>
          ) : null}
        </div>
      );

    default:
      return <div />;
  }
}

/**
 * A plain text row with commit-on-Enter/blur and revert-on-Escape.
 *
 * Uncontrolled between commits: a controlled input bound to the document would
 * fight the user's typing every time an unrelated op landed, and this panel is
 * open while the solver, the copilot and another tab can all be writing.
 *
 * Structured through `Field` so the label names the control and the hint is
 * `aria-describedby` — see the note on the enum row for the bug the wrapping-
 * label version caused.
 */
function TextRow({
  label,
  hint,
  placeholder,
  initial,
  onCommit,
}: {
  label: string;
  hint?: string | undefined;
  placeholder?: string | undefined;
  initial: string;
  onCommit: (raw: string) => void;
}): JSX.Element {
  return (
    <Field label={label} hint={hint}>
      {({ id, describedBy }) => (
        <input
          id={id}
          type="text"
          // `key` on the incoming value: when the model changes underneath, the
          // row remounts with the new value instead of showing a stale draft.
          key={initial}
          defaultValue={initial}
          placeholder={placeholder}
          autoComplete="off"
          spellCheck={false}
          aria-describedby={describedBy}
          className={cn(
            'garh-focus-ring h-8 w-full rounded-md border border-line-strong bg-surface px-2',
            'text-sm text-ink garh-nums',
          )}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              onCommit(e.currentTarget.value);
              e.currentTarget.blur();
            } else if (e.key === 'Escape') {
              e.preventDefault();
              e.currentTarget.value = initial;
              e.currentTarget.blur();
            }
          }}
          onBlur={(e) => {
            if (e.target.value !== initial) onCommit(e.target.value);
          }}
        />
      )}
    </Field>
  );
}
