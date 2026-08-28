/**
 * MeasurePanel.tsx — the DOM half: what is being measured, and everything that
 * has been.
 *
 * It lives in `<CanvasRoot overlay={…}>`, outside the WebGL context, for the
 * reason `CanvasRoot` gives for that prop existing: text in the DOM is
 * accessible, selectable and free. A screen reader can read a measurement out;
 * a troika glyph cannot.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * POINTER EVENTS
 * ────────────────────────────────────────────────────────────────────────────
 * No pointer guard of its own. `CanvasRoot`'s overlay wrapper carries
 * `useOverlayPointerGuard`, whose whole argument is that ONE native listener on
 * the wrapper fixes every present and future panel instead of asking each one
 * to remember — so this panel only has to opt back into hit-testing with
 * `pointer-events-auto`. (`UnderlayPanel` predates the shared guard and still
 * carries its own copy; do not add a third.)
 *
 * ────────────────────────────────────────────────────────────────────────────
 * ONE SOURCE FOR EVERY NUMBER
 * ────────────────────────────────────────────────────────────────────────────
 * Both the live block and the list call `measureReadouts`, the same function
 * the canvas labels are derived from. There is no formatting in this file: the
 * number that flickers under the pointer and the number that persists in the
 * list are literally the same call, so they cannot disagree.
 */

import { useMemo, type JSX } from 'react';

import { Button, Icon, cn } from '@garh/ui';

import { useUnitsDisplay } from '../plot';
import { measureReadouts } from './format';
import { draftPolyline } from './geometry';
import { MEASURE_HINTS, measureBlockReason } from './session';
import { useMeasureStore } from './store';
import { MEASURE_KINDS, type MeasureKind, type Measurement } from './types';

const KIND_LABEL: Readonly<Record<MeasureKind, string>> = {
  distance: 'Distance',
  angle: 'Angle',
  area: 'Area',
};

export interface MeasurePanelProps {
  /** Finish the draft — the mouse equivalent of Enter (§15's "no keyboard-only"). */
  readonly onFinish?: (() => void) | undefined;
  /** Discard the draft. */
  readonly onCancel?: (() => void) | undefined;
  readonly className?: string | undefined;
}

export function MeasurePanel({ onFinish, onCancel, className }: MeasurePanelProps): JSX.Element {
  const display = useUnitsDisplay();
  const kind = useMeasureStore((s) => s.kind);
  const setKind = useMeasureStore((s) => s.setKind);
  const measurements = useMeasureStore((s) => s.measurements);
  const draft = useMeasureStore((s) => s.draft);
  const selectedId = useMeasureStore((s) => s.selectedId);
  const visible = useMeasureStore((s) => s.visible);
  const notice = useMeasureStore((s) => s.notice);
  const dismiss = useMeasureStore((s) => s.dismiss);
  const dismissAll = useMeasureStore((s) => s.dismissAll);
  const select = useMeasureStore((s) => s.select);
  const setVisible = useMeasureStore((s) => s.setVisible);

  // The live numbers, including the rubber-band leg.
  const draftPoints = useMemo(
    () => (draft === null ? [] : draftPolyline(draft.points, draft.cursor)),
    [draft],
  );
  const draftReadouts = useMemo(
    () => (draft === null ? [] : measureReadouts(draft.kind, draftPoints, display)),
    [draft, draftPoints, display],
  );
  // The Finish button and the session refuse on the SAME answer, so the button
  // can never offer a commit the session will reject. `null` while there is no
  // draft at all, which is also when the button is not rendered.
  const blocked = draft === null ? null : measureBlockReason(draft.kind, draft.points);

  return (
    <div
      className={cn(
        'pointer-events-auto flex w-72 flex-col gap-2 rounded-md border border-line bg-surface/95 p-3 shadow-sm backdrop-blur',
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-1.5">
          <Icon name="ruler" size={14} className="shrink-0 text-ink-subtle" />
          <h3 className="text-xs font-semibold text-ink">Measure</h3>
        </span>
        <button
          type="button"
          className="garh-focus-ring rounded p-1 text-2xs text-ink-subtle"
          aria-pressed={visible}
          onClick={() => setVisible(!visible)}
        >
          {visible ? 'Hide all' : 'Show all'}
        </button>
      </div>

      {/*
        A group of pressed/unpressed buttons rather than `role="radiogroup"`.
        A radiogroup owes the user arrow-key navigation and a single tab stop
        (roving tabindex); claiming the role without implementing it is worse
        for a screen-reader user than not claiming it, because it promises a
        keyboard model that is not there. Three tabbable toggle buttons are
        honest and fully operable.

        `text-brand-fg`, not an invented `text-on-brand`: the foreground for a
        brand-filled surface is a token in `@garh/ui`'s preset, and a class the
        preset does not define compiles to NOTHING — the label would inherit
        `text-ink` on a brand background and fail contrast.
      */}
      <div
        className="flex gap-0.5 rounded border border-line p-0.5"
        role="group"
        aria-label="What to measure"
      >
        {MEASURE_KINDS.map((k) => (
          <button
            key={k}
            type="button"
            aria-pressed={k === kind}
            className={cn(
              'garh-focus-ring flex-1 rounded px-2 py-1 text-2xs font-medium',
              k === kind ? 'bg-brand text-brand-fg' : 'text-ink-muted hover:text-ink',
            )}
            onClick={() => setKind(k)}
          >
            {KIND_LABEL[k]}
          </button>
        ))}
      </div>

      <p className="text-2xs leading-4 text-ink-subtle">
        {draft === null ? MEASURE_HINTS[kind][0] : MEASURE_HINTS[kind][1]}
      </p>

      {draftReadouts.length === 0 ? null : (
        <div className="flex flex-col gap-0.5 rounded border border-line bg-surface p-2">
          {draftReadouts.map((readout) => (
            <div key={readout.id} className="flex items-baseline justify-between gap-2">
              <span className="text-2xs text-ink-subtle">{readout.label}</span>
              <span
                className={cn(
                  'garh-nums text-right',
                  readout.emphasis === true
                    ? 'text-xs font-semibold text-ink'
                    : 'text-2xs text-ink-muted',
                )}
              >
                {readout.value}
              </span>
            </div>
          ))}
        </div>
      )}

      {notice === null ? null : (
        <p className="text-2xs leading-4 text-warn-ink" role="status">
          {notice}
        </p>
      )}

      {draft === null ? null : (
        <div className="flex gap-1">
          <Button
            size="sm"
            variant="secondary"
            disabled={blocked !== null}
            onClick={() => onFinish?.()}
          >
            Finish
          </Button>
          <Button size="sm" variant="ghost" onClick={() => onCancel?.()}>
            Cancel
          </Button>
        </div>
      )}

      {measurements.length === 0 ? (
        <p className="text-2xs leading-4 text-ink-subtle">
          Nothing measured yet. Measurements stay on the drawing until you dismiss them, and are
          never part of the model or the drawing set.
        </p>
      ) : (
        <>
          <ul className="flex flex-col gap-1" aria-label="Measurements">
            {measurements.map((m) => (
              <MeasurementRow
                key={m.id}
                measurement={m}
                selected={m.id === selectedId}
                onSelect={() => select(m.id === selectedId ? null : m.id)}
                onDismiss={() => dismiss(m.id)}
              />
            ))}
          </ul>
          <button
            type="button"
            className="garh-focus-ring self-start rounded p-1 text-2xs text-ink-subtle"
            onClick={() => dismissAll()}
          >
            Dismiss all ({measurements.length})
          </button>
        </>
      )}
    </div>
  );
}

interface MeasurementRowProps {
  readonly measurement: Measurement;
  readonly selected: boolean;
  readonly onSelect: () => void;
  readonly onDismiss: () => void;
}

function MeasurementRow({
  measurement,
  selected,
  onSelect,
  onDismiss,
}: MeasurementRowProps): JSX.Element {
  const display = useUnitsDisplay();
  const readouts = measureReadouts(measurement.kind, measurement.points, display);
  const headline = readouts.find((r) => r.emphasis === true) ?? readouts[0];
  const rest = readouts.filter((r) => r !== headline);

  return (
    <li
      className={cn(
        'flex items-start gap-1 rounded border p-1.5',
        selected ? 'border-brand bg-brand/5' : 'border-line',
      )}
    >
      <button
        type="button"
        className="garh-focus-ring min-w-0 flex-1 rounded text-left"
        aria-pressed={selected}
        onClick={onSelect}
      >
        <span className="block text-2xs text-ink-subtle">{KIND_LABEL[measurement.kind]}</span>
        <span className="garh-nums block text-xs font-semibold text-ink">
          {headline?.value ?? '—'}
        </span>
        {rest.length === 0 ? null : (
          <span className="garh-nums block truncate text-2xs text-ink-muted">
            {rest.map((r) => r.value).join(' · ')}
          </span>
        )}
      </button>
      <button
        type="button"
        className="garh-focus-ring rounded p-1 text-ink-subtle hover:text-fail-ink"
        onClick={onDismiss}
      >
        <Icon name="trash" size={13} title={`Dismiss this ${measurement.kind} measurement`} />
      </button>
    </li>
  );
}
