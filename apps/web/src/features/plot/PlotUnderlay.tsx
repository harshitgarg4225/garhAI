/**
 * The tracing underlay, in the SVG plot editor.
 *
 * A scanned survey or a photographed site plan is the thing an architect wants
 * to trace the BOUNDARY off, but the underlay only ever rendered on the Plan
 * tab's R3F canvas — so the one surface where the boundary is actually drawn
 * could not show it. This is that image layer.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ONE RECORD, TWO SURFACES — AND WHY THEY CANNOT DISAGREE
 * ════════════════════════════════════════════════════════════════════════════
 * Both read `useUnderlayStore`, so this layer and `UnderlayLayer` (the canvas
 * quad) are two views of ONE record: toggling visibility here moves the Plan
 * tab too, and the same debounced PATCH persists it. Nothing about the image is
 * re-derived — in particular the placement convention is copied, not
 * reinvented: `originXMm/originYMm` is the model position of image pixel (0,0),
 * i.e. the scan's TOP-LEFT corner, so the image spans x → x + widthMm and
 * extends DOWNWARD from `originYMm` to `originYMm − heightMm`
 * (`UnderlayLayer.tsx:225` computes the same centre). SVG's y grows downward,
 * which is exactly the image's own direction, so the mapping is one `toSvg` of
 * the top-left corner and a positive width/height in mm.
 *
 * Calibration, upload, move and delete are deliberately NOT here. They are the
 * Plan tab's armed-pointer gestures (`UnderlayPanel`), and a second, subtly
 * different implementation of the two-point calibration algebra is exactly the
 * "two sources of truth" this repo keeps being bitten by. What this surface
 * offers is what tracing a boundary needs: show it, fade it.
 *
 * `mmPerPx` is a float, so the extent is a float — fine, because it never
 * leaves the screen (the module rule: stored geometry is integer mm; label
 * anchors and rendering may be floats).
 */

import { useEffect, useRef, useState } from 'react';

import { Button, cn } from '@garh/ui';

import { useUnderlayStore } from '../underlay';
import type { Viewport } from './viewport';
import { toSvg } from './viewport';

/** The presigned URL is short-lived; one silent re-sign before we complain. */
const REFRESH_ATTEMPTS = 1;

export interface PlotUnderlayImageProps {
  readonly vp: Viewport;
}

/**
 * The `<image>` itself. Mount it as the FIRST child of the editor's `<svg>`:
 * a tracing aid belongs under the grid, the boundary and every handle, and
 * `pointer-events: none` keeps it out of the hit test entirely — dragging a
 * corner that sits over the scan must never grab the scan.
 */
export function PlotUnderlayImage({ vp }: PlotUnderlayImageProps): JSX.Element | null {
  const record = useUnderlayStore((s) => s.record);
  const nonce = useUnderlayStore((s) => s.imageNonce);
  const refreshImageUrl = useUnderlayStore((s) => s.refreshImageUrl);
  const setImageError = useUnderlayStore((s) => s.setImageError);
  const attempts = useRef(0);

  // A fresh URL (or a fresh record) deserves a fresh attempt at loading it.
  useEffect(() => {
    attempts.current = 0;
  }, [nonce, record?.objectKey]);

  if (!record?.visible) return null;

  const widthMm = record.widthPx * record.mmPerPx;
  const heightMm = record.heightPx * record.mmPerPx;
  // Pixel (0,0) is the scan's top-left: the image hangs DOWN from originYMm.
  const topLeft = toSvg(vp, { x: record.originXMm, y: record.originYMm });

  return (
    <image
      data-testid="plot-underlay-image"
      href={record.imageUrl}
      x={topLeft.x}
      y={topLeft.y}
      width={widthMm}
      height={heightMm}
      opacity={record.opacity}
      preserveAspectRatio="none"
      className="pointer-events-none select-none"
      onError={() => {
        // §13 signs these for ~10 minutes. An expired link must re-sign once
        // and then SAY so — a blank editor with no explanation is the failure
        // mode this handler exists to prevent.
        if (attempts.current >= REFRESH_ATTEMPTS) {
          setImageError(
            'The scan could not be loaded. Open the Plan tab to re-upload it, or reload the page.',
          );
          return;
        }
        attempts.current += 1;
        void refreshImageUrl().then((ok) => {
          if (!ok) {
            setImageError(
              'The scan could not be loaded. Open the Plan tab to re-upload it, or reload the page.',
            );
          }
        });
      }}
    />
  );
}

export interface PlotUnderlayControlsProps {
  className?: string | undefined;
}

/**
 * Show / hide and fade. Nothing renders at all when the project has no
 * underlay: an affordance for a file that does not exist teaches nothing, and
 * uploading one is the Plan tab's job.
 */
export function PlotUnderlayControls({ className }: PlotUnderlayControlsProps): JSX.Element | null {
  const record = useUnderlayStore((s) => s.record);
  const imageError = useUnderlayStore((s) => s.imageError);
  const patch = useUnderlayStore((s) => s.patch);
  const flush = useUnderlayStore((s) => s.flush);
  const [showFade, setShowFade] = useState(false);

  if (record === null) return null;

  return (
    <span className={cn('inline-flex items-center gap-1.5', className)}>
      <Button
        variant="ghost"
        size="sm"
        iconLeft="image"
        data-testid="plot-underlay-toggle"
        onClick={() => {
          patch({ visible: !record.visible });
          flush();
        }}
      >
        {record.visible ? 'Hide scan' : 'Show scan'}
      </Button>
      {record.visible ? (
        <>
          <Button
            variant="ghost"
            size="sm"
            aria-expanded={showFade}
            data-testid="plot-underlay-fade"
            onClick={() => setShowFade((v) => !v)}
          >
            Fade
          </Button>
          {showFade ? (
            <label className="flex items-center gap-1 text-2xs text-ink-subtle">
              <span className="sr-only">Scan opacity</span>
              <input
                type="range"
                min={5}
                max={100}
                step={5}
                value={Math.round(record.opacity * 100)}
                aria-label="Scan opacity"
                onChange={(e) => patch({ opacity: Number(e.target.value) / 100 })}
                onPointerUp={() => flush()}
                onBlur={() => flush()}
                className="h-1 w-24 accent-brand"
              />
              <span className="garh-nums w-8">{Math.round(record.opacity * 100)}%</span>
            </label>
          ) : null}
        </>
      ) : null}
      {imageError === null ? null : (
        <span role="alert" className="text-2xs text-fail-ink">
          {imageError}
        </span>
      )}
    </span>
  );
}
