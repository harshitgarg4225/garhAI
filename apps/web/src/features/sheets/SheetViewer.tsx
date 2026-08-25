/**
 * SheetViewer.tsx — the zoomable sheet viewer (§7, F7-A).
 *
 * A drawing is read by zooming into a dimension string, so this is a real pan/zoom
 * surface, not an `<img>`: scroll to zoom about the cursor, drag to pan, `+`/`-`/`0`
 * from the keyboard, and a "fit" that restores the whole A2 sheet.
 *
 * How the SVG gets on screen, and why
 * -----------------------------------
 * The server hands over markup (`GET /sheets/:id/content`), and this component puts it
 * in the document with `dangerouslySetInnerHTML`. That name deserves an answer:
 *
 *   - the SVG is produced by our own renderer, which escapes every text node;
 *   - the worker runs an element/attribute **allowlist** over the finished document
 *     before storing it (§13);
 *   - the API runs the dangerous-token check again on the way out, because between
 *     those two moments the bytes sat in an object store;
 *   - {@link assertRenderableSvg} below runs a third check in the browser, and refuses
 *     to mount anything that fails.
 *
 * The alternative — an `<img src>` or an `<iframe>` — would be inert but unusable: the
 * markup has to be in the document for zoom to stay crisp and for a future annotation
 * layer to hit-test it. Three independent checks on our own output is the trade.
 *
 * Zoom is applied to a wrapper's `transform`, never to the SVG's `viewBox`: the SVG is
 * print-true (`width="594mm"`), and rewriting its dimensions would silently break the
 * one property that makes these sheets submittable.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { Button, Icon, Spinner, cn } from '@garh/ui';

import { AppError } from '../../lib/errors';
import { fetchSheetContent, type SheetContent } from './api';

const MIN_ZOOM = 0.1;
const MAX_ZOOM = 12;
const ZOOM_STEP = 1.25;

/**
 * Tokens that must never appear in a sheet we are about to put in the DOM.
 *
 * A mirror of the worker's `sanitize.py` allowlist, reduced to its dangerous half —
 * the browser cannot import the Python module, and a full grammar check here would be
 * a second implementation to keep in sync. Defence in depth, not the primary control:
 * if this ever fires, something upstream is broken and the viewer says so loudly
 * rather than rendering "most of" a compromised drawing.
 */
const FORBIDDEN = [
  '<script',
  '<foreignobject',
  '<iframe',
  '<object',
  '<embed',
  '<use',
  'javascript:',
  'onload=',
  'onerror=',
  'onclick=',
  '<!entity',
] as const;

export function assertRenderableSvg(svg: string): void {
  const lowered = svg.toLowerCase();
  for (const token of FORBIDDEN) {
    if (lowered.includes(token)) {
      throw new Error(`sheet SVG contains a forbidden construct (${token})`);
    }
  }
  if (!lowered.trimStart().startsWith('<svg')) {
    throw new Error('sheet content is not an SVG document');
  }
}

export interface SheetViewerProps {
  projectId: string;
  sheetId: string;
  /** Shown in the toolbar while the drawing loads, so the header never flickers. */
  label?: string;
  className?: string;
}

interface ViewState {
  zoom: number;
  x: number;
  y: number;
}

const FIT: ViewState = { zoom: 1, x: 0, y: 0 };

export function SheetViewer({
  projectId,
  sheetId,
  label,
  className,
}: SheetViewerProps): JSX.Element {
  const [content, setContent] = useState<SheetContent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<ViewState>(FIT);
  const frameRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setContent(null);
    setError(null);
    setView(FIT);
    fetchSheetContent(projectId, sheetId, controller.signal)
      .then((next) => {
        assertRenderableSvg(next.svg);
        setContent(next);
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setError(
          cause instanceof AppError
            ? `${cause.message} ${cause.action}`.trim()
            : cause instanceof Error
              ? cause.message
              : 'We could not open that sheet.',
        );
      });
    return () => controller.abort();
  }, [projectId, sheetId]);

  const zoomAbout = useCallback((factor: number, clientX?: number, clientY?: number) => {
    setView((current) => {
      const next = clamp(current.zoom * factor, MIN_ZOOM, MAX_ZOOM);
      const frame = frameRef.current;
      if (!frame || clientX === undefined || clientY === undefined) {
        return { ...current, zoom: next };
      }
      // Keep the point under the cursor fixed: without this, zooming into a dimension
      // string walks it off the screen and the viewer feels broken.
      const rect = frame.getBoundingClientRect();
      const px = clientX - rect.left;
      const py = clientY - rect.top;
      const ratio = next / current.zoom;
      return {
        zoom: next,
        x: px - (px - current.x) * ratio,
        y: py - (py - current.y) * ratio,
      };
    });
  }, []);

  const onWheel = useCallback(
    (event: React.WheelEvent<HTMLDivElement>) => {
      // No preventDefault: React attaches wheel passively, so calling it warns and
      // does nothing. The container sets `overscroll-behavior: contain` in CSS
      // instead, which is what actually stops the page scrolling behind the sheet.
      zoomAbout(event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP, event.clientX, event.clientY);
    },
    [zoomAbout],
  );

  const onPointerDown = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = { x: event.clientX, y: event.clientY, ox: 0, oy: 0 };
    setView((current) => {
      dragRef.current = { x: event.clientX, y: event.clientY, ox: current.x, oy: current.y };
      return current;
    });
  }, []);

  const onPointerMove = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    setView((current) => ({
      ...current,
      x: drag.ox + (event.clientX - drag.x),
      y: drag.oy + (event.clientY - drag.y),
    }));
  }, []);

  const endDrag = useCallback(() => {
    dragRef.current = null;
  }, []);

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (event.key === '+' || event.key === '=') {
        zoomAbout(ZOOM_STEP);
      } else if (event.key === '-' || event.key === '_') {
        zoomAbout(1 / ZOOM_STEP);
      } else if (event.key === '0') {
        setView(FIT);
      } else {
        return;
      }
      event.preventDefault();
    },
    [zoomAbout],
  );

  const caption = useMemo(() => {
    if (!content) return label ?? 'Loading…';
    const scale = content.scaleDenominator ? `1:${content.scaleDenominator}` : '';
    return [content.number, content.title, scale].filter(Boolean).join(' · ');
  }, [content, label]);

  return (
    <div className={cn('flex h-full flex-col overflow-hidden rounded-md border border-line bg-surface', className)}>
      <div className="flex items-center gap-2 border-b border-line px-3 py-2">
        <span className="truncate text-xs font-medium text-ink" data-testid="sheet-viewer-caption">
          {caption}
        </span>
        <span className="ml-auto flex items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            aria-label="Zoom out"
            onClick={() => zoomAbout(1 / ZOOM_STEP)}
          >
            <Icon name="minus" size={14} />
          </Button>
          <span className="w-12 text-center font-mono text-2xs tabular-nums text-ink-muted">
            {Math.round(view.zoom * 100)}%
          </span>
          <Button size="sm" variant="ghost" aria-label="Zoom in" onClick={() => zoomAbout(ZOOM_STEP)}>
            <Icon name="plus" size={14} />
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setView(FIT)}>
            Fit
          </Button>
        </span>
      </div>

      <div
        ref={frameRef}
        role="img"
        aria-label={caption}
        tabIndex={0}
        data-testid="sheet-viewer"
        className="relative flex-1 cursor-grab overflow-hidden bg-canvas [overscroll-behavior:contain] focus:outline-none focus-visible:ring-2 focus-visible:ring-brand active:cursor-grabbing"
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onKeyDown={onKeyDown}
      >
        {error ? (
          <div className="flex h-full items-center justify-center p-6 text-center">
            <p className="max-w-sm text-xs leading-5 text-danger" data-testid="sheet-viewer-error">
              {error}
            </p>
          </div>
        ) : !content ? (
          <div className="flex h-full items-center justify-center gap-2 text-xs text-ink-muted">
            <Spinner size={14} /> Opening the drawing…
          </div>
        ) : (
          <div
            className="origin-top-left will-change-transform"
            style={{
              transform: `translate(${view.x}px, ${view.y}px) scale(${view.zoom})`,
            }}
            // Reviewed above: our own renderer's output, allowlisted by the worker,
            // re-checked by the API, and checked again by `assertRenderableSvg`
            // before this line runs.
            dangerouslySetInnerHTML={{ __html: content.svg }}
          />
        )}
      </div>

      {content ? (
        <p className="border-t border-line px-3 py-1.5 text-2xs text-ink-muted">
          Every dimension is in millimetres, whatever the units toggle shows — that is the
          drafting convention, and it keeps chains summing exactly.
        </p>
      ) : null}
    </div>
  );
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

export default SheetViewer;
