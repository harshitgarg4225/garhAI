/**
 * UnderlayPanel.tsx — the DOM half of the tracing underlay.
 *
 * A collapsible card in the plan overlay (the SunPanel's visual language:
 * `border-line`, `bg-surface/95`, 2xs labels, `garh-nums` on every figure)
 * plus, while a mode is armed, a full-canvas capture layer.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY THE CAPTURE LAYER EXISTS, AND WHY IT IS A NATIVE LISTENER
 * ════════════════════════════════════════════════════════════════════════════
 * `UnderlayLayer` is deliberately not in `PickRegistry`, so there is nothing to
 * click on the image itself. Calibrating and repositioning both need raw
 * pointer positions on the drawing plane, so they are ARMED MODES that take the
 * pointer for as long as they are on, and say so in a banner.
 *
 * `CanvasRoot` attaches its pointer handling as NATIVE listeners on the canvas
 * container, which is an ancestor of this overlay. React's synthetic handlers
 * run at the React root — an ancestor of that — so `stopPropagation` from a
 * React `onPointerDown` would fire far too late: the wall tool would already
 * have seen the press. The listeners below are therefore native and attached to
 * the capture element itself, where stopping propagation actually stops the
 * event reaching the canvas.
 *
 * What is deliberately NOT swallowed:
 *   · the wheel — so the architect can still zoom in to place a mark precisely;
 *   · middle-button presses — so panning still works mid-calibration.
 * Both simply bubble through to the canvas's own listeners untouched.
 *
 * No raycasting happens here and no registry is consulted. `pixelToMmF` is the
 * viewport's own pixel → plane conversion (the same one `PlanPage` uses for the
 * furniture drag-and-drop), which is why an armed mode costs no pick candidates
 * and cannot change what a click on a wall means once the mode is off.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { roundMm } from '@garh/model';
import { Button, Icon, LengthInput, cn } from '@garh/ui';

import type { CanvasCore } from '../canvas/core';
import { useUnitsDisplay } from '../plot';
import { formatLengthDisplay, formatIndianNumber } from '../../lib/units';
import {
  calibrationRefusalText,
  markDistanceMm,
  recalibrate,
  underlayExtentMm,
  type MarkMm,
} from './calibration';
import { useUnderlayStore } from './store';

/**
 * Keep this element's pointer events out of the canvas's tools.
 *
 * `CanvasRoot` listens for `pointerdown`/`move`/`up` NATIVELY on the canvas
 * container, and every overlay panel is a descendant of that container. React
 * delegates its own handlers to the app root — an ancestor of the container —
 * so a React `onPointerDown` with `stopPropagation` runs strictly after the
 * canvas has already seen the press: with the wall tool armed, clicking a
 * button in this panel would also drop a wall point behind it.
 *
 * The listener therefore has to be native and attached HERE, where stopping
 * propagation still gets ahead of the container. Only the three pointer events
 * are stopped:
 *   · `click` is deliberately left alone — it is what React's delegation
 *     carries, so every `onClick` in the panel keeps working, and the canvas
 *     synthesises its own click from pointerdown/up rather than reading it;
 *   · `wheel` is left alone so the wheel still zooms the drawing under the
 *     cursor, exactly as it does over the rest of the canvas.
 * Nothing calls `preventDefault`, so focus, caret placement and the opacity
 * slider's own drag all behave normally.
 */
function useSwallowCanvasPointer(element: HTMLElement | null): void {
  useEffect(() => {
    if (element === null) return undefined;
    const stop = (event: Event): void => event.stopPropagation();
    element.addEventListener('pointerdown', stop);
    element.addEventListener('pointermove', stop);
    element.addEventListener('pointerup', stop);
    return () => {
      element.removeEventListener('pointerdown', stop);
      element.removeEventListener('pointermove', stop);
      element.removeEventListener('pointerup', stop);
    };
  }, [element]);
}

/** Nudge steps, mm. One module-ish and one "I scanned it crooked" step. */
const NUDGE_SMALL_MM = 10;
const NUDGE_LARGE_MM = 100;

/** Only PNG and JPEG — the two formats the server's magic-byte sniff accepts. */
const ACCEPTED_IMAGE_TYPES = 'image/png,image/jpeg';

export interface UnderlayPanelProps {
  readonly projectId: string;
  /**
   * The live canvas core. `null` until `CanvasRoot` hands it over, which is
   * also the honest disabled state for the two modes that need pixel → mm.
   */
  readonly core: CanvasCore | null;
  readonly className?: string | undefined;
}

export function UnderlayPanel({ projectId, core, className }: UnderlayPanelProps): JSX.Element {
  const display = useUnitsDisplay();
  const record = useUnderlayStore((s) => s.record);
  const loading = useUnderlayStore((s) => s.loading);
  const busy = useUnderlayStore((s) => s.busy);
  const error = useUnderlayStore((s) => s.error);
  const imageError = useUnderlayStore((s) => s.imageError);
  const mode = useUnderlayStore((s) => s.mode);
  const marks = useUnderlayStore((s) => s.marks);
  const load = useUnderlayStore((s) => s.load);
  const upload = useUnderlayStore((s) => s.upload);
  const patch = useUnderlayStore((s) => s.patch);
  const remove = useUnderlayStore((s) => s.remove);
  const setMode = useUnderlayStore((s) => s.setMode);
  const reset = useUnderlayStore((s) => s.reset);

  const [open, setOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const [panelEl, setPanelEl] = useState<HTMLDivElement | null>(null);
  useSwallowCanvasPointer(panelEl);

  // Fetch on mount and whenever the project changes; flush any debounced patch
  // and drop the local mode when the tab goes away.
  useEffect(() => {
    void load(projectId);
    return () => {
      useUnderlayStore.getState().flush();
      reset();
    };
  }, [projectId, load, reset]);

  const onFilePicked = (files: FileList | null): void => {
    const file = files?.[0];
    if (file !== undefined) {
      void upload(file);
      setOpen(true);
    }
    // Allow re-picking the same file after a rejected upload.
    if (fileRef.current !== null) fileRef.current.value = '';
  };

  const nudge = (dxMm: number, dyMm: number): void => {
    if (record === null) return;
    patch({ originXMm: record.originXMm + dxMm, originYMm: record.originYMm + dyMm });
  };

  const locked = record?.locked ?? false;
  const canAim = core !== null && record !== null && !locked;

  return (
    <>
      {mode !== 'off' && core !== null && record !== null ? (
        <UnderlayCaptureLayer core={core} />
      ) : null}

      <div
        ref={setPanelEl}
        className={cn(
          'pointer-events-auto flex w-72 flex-col gap-2 rounded-md border border-line bg-surface/95 p-3 shadow-sm backdrop-blur',
          className,
        )}
      >
        <div className="flex items-center justify-between gap-2">
          <button
            type="button"
            className="garh-focus-ring -m-1 flex min-w-0 items-center gap-1.5 rounded p-1 text-left"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            <Icon name="image" size={14} className="shrink-0 text-ink-subtle" />
            <h3 className="text-xs font-semibold text-ink">Underlay</h3>
            <Icon
              name={open ? 'chevron-down' : 'chevron-up'}
              size={14}
              className="shrink-0 text-ink-subtle"
            />
          </button>
          {record === null ? null : (
            <span className="shrink-0 text-2xs text-ink-subtle garh-nums">
              {record.visible ? `${Math.round(record.opacity * 100)}%` : 'hidden'}
              {record.locked ? ' · locked' : ''}
            </span>
          )}
        </div>

        <input
          ref={fileRef}
          type="file"
          accept={ACCEPTED_IMAGE_TYPES}
          className="sr-only"
          aria-label="Plan image to trace over"
          onChange={(e) => onFilePicked(e.target.files)}
        />

        {open ? (
          <>
            {record === null ? (
              <div className="flex flex-col items-start gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  iconLeft="image"
                  disabled={busy || loading}
                  onClick={() => fileRef.current?.click()}
                >
                  {busy ? 'Uploading…' : 'Upload image'}
                </Button>
                <p className="text-2xs leading-4 text-ink-subtle">
                  A PNG or JPEG of a survey, a sketch or an existing plan. Drop it under the
                  drawing, set its scale from two points you know the distance between, then trace
                  over it. It is a view aid only — it never enters the model, the drawings or an
                  export.
                </p>
              </div>
            ) : (
              <UnderlayBody
                display={display}
                widthPx={record.widthPx}
                heightPx={record.heightPx}
                mmPerPx={record.mmPerPx}
                opacity={record.opacity}
                visible={record.visible}
                locked={record.locked}
                busy={busy}
                mode={mode}
                canAim={canAim}
                onOpacity={(value) => patch({ opacity: value })}
                onVisible={(value) => patch({ visible: value })}
                onLocked={(value) => patch({ locked: value })}
                onCalibrate={() => setMode(mode === 'calibrate' ? 'off' : 'calibrate')}
                onMove={() => setMode(mode === 'move' ? 'off' : 'move')}
                onNudge={nudge}
                onReplace={() => fileRef.current?.click()}
                onRemove={() => void remove()}
              />
            )}

            {imageError === null ? null : (
              <p className="text-2xs leading-4 text-warn-ink" role="status">
                {imageError}
              </p>
            )}
            {error === null ? null : (
              <p className="text-2xs leading-4 text-fail-ink" role="alert">
                {error}
              </p>
            )}
          </>
        ) : null}
      </div>

      {/* The distance prompt, once both marks are down. Sits beside the panel
          rather than in it so it cannot be scrolled or collapsed away. */}
      {mode === 'calibrate' && record !== null && marks.length >= 2 ? (
        <CalibratePrompt display={display} />
      ) : null}
    </>
  );
}

// ---------------------------------------------------------------------------
// The body of the card, once there is an underlay
// ---------------------------------------------------------------------------

interface UnderlayBodyProps {
  readonly display: ReturnType<typeof useUnitsDisplay>;
  readonly widthPx: number;
  readonly heightPx: number;
  readonly mmPerPx: number;
  readonly opacity: number;
  readonly visible: boolean;
  readonly locked: boolean;
  readonly busy: boolean;
  readonly mode: 'off' | 'calibrate' | 'move';
  readonly canAim: boolean;
  readonly onOpacity: (value: number) => void;
  readonly onVisible: (value: boolean) => void;
  readonly onLocked: (value: boolean) => void;
  readonly onCalibrate: () => void;
  readonly onMove: () => void;
  readonly onNudge: (dxMm: number, dyMm: number) => void;
  readonly onReplace: () => void;
  readonly onRemove: () => void;
}

function UnderlayBody(props: UnderlayBodyProps): JSX.Element {
  const extent = underlayExtentMm(props.widthPx, props.heightPx, props.mmPerPx);
  return (
    <>
      {/* The scale, stated two ways: the raw ratio and the size it implies.
          The second is the one an architect can falsify at a glance. */}
      <div className="flex flex-col gap-0.5 text-2xs leading-4 text-ink-muted garh-nums">
        <span>
          {formatIndianNumber(props.widthPx)} × {formatIndianNumber(props.heightPx)} px · 1 px ={' '}
          {props.mmPerPx.toFixed(3)} mm
        </span>
        <span className="text-ink-subtle">
          Covers {formatLengthDisplay(roundMm(extent.widthMm), props.display)} ×{' '}
          {formatLengthDisplay(roundMm(extent.heightMm), props.display)}
        </span>
      </div>

      <label className="block">
        <span className="mb-1 flex items-baseline justify-between text-2xs font-medium text-ink-muted">
          <span>Opacity</span>
          <span className="text-ink garh-nums">{Math.round(props.opacity * 100)}%</span>
        </span>
        <input
          type="range"
          min={5}
          max={100}
          step={5}
          value={Math.round(props.opacity * 100)}
          className="w-full"
          aria-label="Underlay opacity"
          onChange={(e) => props.onOpacity(Number(e.target.value) / 100)}
        />
      </label>

      <div className="flex flex-wrap gap-1">
        <Button
          size="sm"
          variant={props.visible ? 'secondary' : 'ghost'}
          iconLeft="layers"
          aria-pressed={props.visible}
          onClick={() => props.onVisible(!props.visible)}
        >
          {props.visible ? 'Visible' : 'Hidden'}
        </Button>
        <Button
          size="sm"
          variant={props.locked ? 'secondary' : 'ghost'}
          iconLeft="lock"
          aria-pressed={props.locked}
          onClick={() => props.onLocked(!props.locked)}
        >
          {props.locked ? 'Locked' : 'Unlocked'}
        </Button>
      </div>

      <div className="flex flex-wrap gap-1">
        <Button
          size="sm"
          variant={props.mode === 'calibrate' ? 'primary' : 'secondary'}
          iconLeft="ruler"
          disabled={!props.canAim}
          onClick={props.onCalibrate}
        >
          {props.mode === 'calibrate' ? 'Marking…' : 'Calibrate scale'}
        </Button>
        <Button
          size="sm"
          variant={props.mode === 'move' ? 'primary' : 'secondary'}
          iconLeft="cursor"
          disabled={!props.canAim}
          onClick={props.onMove}
        >
          {props.mode === 'move' ? 'Moving…' : 'Move'}
        </Button>
      </div>

      {props.locked ? (
        <p className="text-2xs leading-4 text-ink-subtle">
          Locked, so it cannot be moved or rescaled by accident while you trace. Unlock to
          reposition or recalibrate it.
        </p>
      ) : (
        <NudgePad onNudge={props.onNudge} />
      )}

      <div className="flex flex-wrap gap-1 border-t border-line pt-2">
        <Button
          size="sm"
          variant="ghost"
          iconLeft="refresh"
          disabled={props.busy}
          onClick={props.onReplace}
        >
          Replace
        </Button>
        <Button
          size="sm"
          variant="ghost"
          iconLeft="trash"
          disabled={props.busy}
          onClick={props.onRemove}
        >
          Remove
        </Button>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Nudge pad — the always-available half of positioning
// ---------------------------------------------------------------------------

/**
 * Arrow nudges in model millimetres.
 *
 * Not a poor relation of dragging: a scan is usually within a few centimetres
 * after one drag and the last correction is easier typed than aimed. Hold Shift
 * (or use the ± row) for the coarse step. North is up, matching the plan.
 */
function NudgePad({ onNudge }: { onNudge: (dxMm: number, dyMm: number) => void }): JSX.Element {
  const [coarse, setCoarse] = useState(false);
  const step = coarse ? NUDGE_LARGE_MM : NUDGE_SMALL_MM;
  const cell =
    'garh-focus-ring flex h-7 items-center justify-center rounded border border-line text-ink-muted hover:bg-surface-sunken';

  return (
    <div className="flex items-center gap-2">
      <div className="grid w-24 shrink-0 grid-cols-3 gap-0.5">
        <span />
        <button
          type="button"
          className={cell}
          aria-label={`Nudge north ${step} mm`}
          onClick={() => onNudge(0, step)}
        >
          <Icon name="chevron-up" size={14} />
        </button>
        <span />
        <button
          type="button"
          className={cell}
          aria-label={`Nudge west ${step} mm`}
          onClick={() => onNudge(-step, 0)}
        >
          <Icon name="chevron-left" size={14} />
        </button>
        <span />
        <button
          type="button"
          className={cell}
          aria-label={`Nudge east ${step} mm`}
          onClick={() => onNudge(step, 0)}
        >
          <Icon name="chevron-right" size={14} />
        </button>
        <span />
        <button
          type="button"
          className={cell}
          aria-label={`Nudge south ${step} mm`}
          onClick={() => onNudge(0, -step)}
        >
          <Icon name="chevron-down" size={14} />
        </button>
        <span />
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-2xs text-ink-subtle">Step</span>
        <div className="flex gap-1">
          <Button
            size="sm"
            variant={coarse ? 'ghost' : 'secondary'}
            aria-pressed={!coarse}
            onClick={() => setCoarse(false)}
          >
            {NUDGE_SMALL_MM} mm
          </Button>
          <Button
            size="sm"
            variant={coarse ? 'secondary' : 'ghost'}
            aria-pressed={coarse}
            onClick={() => setCoarse(true)}
          >
            {NUDGE_LARGE_MM} mm
          </Button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The known-distance prompt
// ---------------------------------------------------------------------------

/**
 * "How far apart are those two marks, really?"
 *
 * Follows the DXF importer's honesty rules: the current reading is shown before
 * anything is typed, the result is previewed BEFORE it is applied (so a slipped
 * decimal point is visible as an absurd sheet size rather than discovered three
 * hours later), and a refusal explains itself instead of clamping.
 */
function CalibratePrompt({
  display,
}: {
  display: ReturnType<typeof useUnitsDisplay>;
}): JSX.Element | null {
  const record = useUnderlayStore((s) => s.record);
  const marks = useUnderlayStore((s) => s.marks);
  const clearMarks = useUnderlayStore((s) => s.clearMarks);
  const setMode = useUnderlayStore((s) => s.setMode);
  const applyCalibration = useUnderlayStore((s) => s.applyCalibration);

  const [knownMm, setKnownMm] = useState<number | null>(null);
  const [promptEl, setPromptEl] = useState<HTMLDivElement | null>(null);
  useSwallowCanvasPointer(promptEl);
  const a = marks[0];
  const b = marks[1];

  // A fresh pair of marks starts a fresh answer — carrying the previous
  // distance over would silently recalibrate against the wrong run.
  useEffect(() => {
    setKnownMm(null);
  }, [a, b]);

  if (record === null || a === undefined || b === undefined) return null;

  const measuredMm = markDistanceMm(a, b);
  const result =
    knownMm === null
      ? null
      : recalibrate({
          a,
          b,
          current: {
            mmPerPx: record.mmPerPx,
            originXMm: record.originXMm,
            originYMm: record.originYMm,
          },
          knownMm,
        });

  const preview =
    result?.ok === true
      ? underlayExtentMm(record.widthPx, record.heightPx, result.next.mmPerPx)
      : null;

  return (
    <div
      ref={setPromptEl}
      data-underlay-chrome=""
      className="pointer-events-auto absolute left-1/2 top-16 z-10 w-80 -translate-x-1/2 rounded-md border border-brand/40 bg-surface p-3 shadow-lg"
    >
      <h4 className="text-xs font-semibold text-ink">How far apart are those two marks?</h4>
      <p className="mt-0.5 text-2xs leading-4 text-ink-muted garh-nums">
        They currently read {formatLengthDisplay(roundMm(measuredMm), display)} —{' '}
        {(measuredMm / record.mmPerPx).toFixed(0)} px on the scan.
      </p>

      <div className="mt-2">
        <LengthInput
          label="Real distance"
          valueMm={knownMm}
          display={display}
          onCommitMm={setKnownMm}
          minMm={1}
          autoFocus
          placeholder={`e.g. 12'6", 3.8m or 3810`}
        />
      </div>

      {result === null ? null : result.ok ? (
        <p className="mt-2 text-2xs leading-4 text-ink-muted garh-nums">
          New scale: 1 px = {result.next.mmPerPx.toFixed(3)} mm ({result.factor.toFixed(3)}× the
          current one). The scan would cover{' '}
          <span className="text-ink">
            {formatLengthDisplay(roundMm(preview?.widthMm ?? 0), display)} ×{' '}
            {formatLengthDisplay(roundMm(preview?.heightMm ?? 0), display)}
          </span>
          .
        </p>
      ) : (
        <p className="mt-2 text-2xs leading-4 text-fail-ink" role="alert">
          {calibrationRefusalText(result.reason)}
        </p>
      )}

      <div className="mt-2.5 flex justify-end gap-1">
        <Button size="sm" variant="ghost" onClick={() => setMode('off')}>
          Cancel
        </Button>
        <Button size="sm" variant="ghost" iconLeft="refresh" onClick={() => clearMarks()}>
          Re-mark
        </Button>
        <Button
          size="sm"
          variant="primary"
          iconLeft="check"
          disabled={result?.ok !== true}
          onClick={() => {
            if (result?.ok === true) applyCalibration(result.next);
          }}
        >
          Apply scale
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The capture layer — see the header for why these listeners are native
// ---------------------------------------------------------------------------

function UnderlayCaptureLayer({ core }: { core: CanvasCore }): JSX.Element {
  const mode = useUnderlayStore((s) => s.mode);
  const marks = useUnderlayStore((s) => s.marks);
  const [element, setElement] = useState<HTMLDivElement | null>(null);

  /** Drag bookkeeping for `move`: where the press landed, and the origin then. */
  const dragRef = useRef<{ fromMm: MarkMm; originXMm: number; originYMm: number } | null>(null);

  const toMm = useCallback(
    (event: PointerEvent, host: HTMLElement): MarkMm | null => {
      const rect = host.getBoundingClientRect();
      // The viewport's own pixel → plane conversion. No raycast, no registry.
      return core.viewport.pixelToMmF({
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
      });
    },
    [core],
  );

  useEffect(() => {
    if (element === null) return undefined;

    /** Chrome drawn inside the capture area must not also place a mark. */
    const isChrome = (event: Event): boolean =>
      event.target instanceof Element && event.target.closest('[data-underlay-chrome]') !== null;

    const onDown = (event: PointerEvent): void => {
      // Middle/right stay with the canvas so panning survives an armed mode.
      if (event.button !== 0) return;
      event.stopPropagation();
      if (isChrome(event)) return;
      event.preventDefault();

      const store = useUnderlayStore.getState();
      const point = toMm(event, element);
      if (point === null) return;

      if (store.mode === 'calibrate') {
        // A third click starts over rather than silently ignoring the press.
        if (store.marks.length >= 2) store.clearMarks();
        store.addMark(point);
        return;
      }
      if (store.mode === 'move' && store.record !== null && !store.record.locked) {
        dragRef.current = {
          fromMm: point,
          originXMm: store.record.originXMm,
          originYMm: store.record.originYMm,
        };
        element.setPointerCapture(event.pointerId);
      }
    };

    const onMove = (event: PointerEvent): void => {
      event.stopPropagation();
      const drag = dragRef.current;
      if (drag === null) return;
      const point = toMm(event, element);
      if (point === null) return;
      // Integer mm at the boundary: this becomes an `originXMm` on the wire,
      // where the server's `Mm` is a StrictInt.
      useUnderlayStore.getState().patch({
        originXMm: roundMm(drag.originXMm + (point.x - drag.fromMm.x)),
        originYMm: roundMm(drag.originYMm + (point.y - drag.fromMm.y)),
      });
    };

    const onUp = (event: PointerEvent): void => {
      if (event.button !== 0) return;
      event.stopPropagation();
      if (dragRef.current !== null) {
        dragRef.current = null;
        if (element.hasPointerCapture(event.pointerId)) {
          element.releasePointerCapture(event.pointerId);
        }
        // The drag is over; do not make the architect wait 400 ms to find out
        // whether the position stuck.
        useUnderlayStore.getState().flush();
      }
    };

    /** Clicks and double-clicks are consumed wholesale while a mode is armed. */
    const onSwallow = (event: MouseEvent): void => {
      if (isChrome(event)) return;
      event.stopPropagation();
    };

    const onKey = (event: KeyboardEvent): void => {
      if (event.key !== 'Escape') return;
      event.stopPropagation();
      useUnderlayStore.getState().setMode('off');
    };

    element.addEventListener('pointerdown', onDown);
    element.addEventListener('pointermove', onMove);
    element.addEventListener('pointerup', onUp);
    element.addEventListener('pointercancel', onUp);
    element.addEventListener('click', onSwallow);
    element.addEventListener('dblclick', onSwallow);
    window.addEventListener('keydown', onKey, true);

    return () => {
      element.removeEventListener('pointerdown', onDown);
      element.removeEventListener('pointermove', onMove);
      element.removeEventListener('pointerup', onUp);
      element.removeEventListener('pointercancel', onUp);
      element.removeEventListener('click', onSwallow);
      element.removeEventListener('dblclick', onSwallow);
      window.removeEventListener('keydown', onKey, true);
      dragRef.current = null;
    };
  }, [element, toMm]);

  const hint = useMemo(() => {
    if (mode === 'move') return 'Drag the underlay into place. Wheel still zooms; Esc when done.';
    if (marks.length === 0)
      return 'Click the first of two points you know the real distance between.';
    if (marks.length === 1) return 'Now click the second point — the further apart, the better.';
    return 'Type the real distance between the marks, or click again to re-mark.';
  }, [mode, marks.length]);

  return (
    <div
      ref={setElement}
      className={cn(
        'pointer-events-auto absolute inset-0',
        mode === 'move' ? 'cursor-move' : 'cursor-crosshair',
      )}
    >
      <div
        data-underlay-chrome=""
        className="pointer-events-auto absolute left-1/2 top-3 flex -translate-x-1/2 items-center gap-2 rounded-md border border-brand/40 bg-surface px-3 py-1.5 text-xs text-ink shadow-lg"
        role="status"
      >
        <Icon name={mode === 'move' ? 'cursor' : 'ruler'} size={14} className="text-brand" />
        <span>{hint}</span>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => useUnderlayStore.getState().setMode('off')}
        >
          Done
        </Button>
      </div>
    </div>
  );
}

export default UnderlayPanel;
