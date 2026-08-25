/**
 * SheetThumbnail.tsx — a sheet card in the Sheets tab grid.
 *
 * The thumbnail is the **real drawing**, scaled down, not a placeholder icon: the
 * whole point of the grid is recognising A-02A from A-02B at a glance, and a generic
 * page icon repeated ten times does not do that.
 *
 * It is fetched lazily through an `IntersectionObserver`, because a G+2 set is a dozen
 * sheets and downloading every SVG on mount would cost a quarter of a megabyte before
 * the architect has scrolled. Until a card is near the viewport it shows a skeleton —
 * §15's "skeletons everywhere, never blank, never spinner-only".
 *
 * The SVG is inlined for the same reasons `SheetViewer` inlines it, with the same
 * three-layer check; `assertRenderableSvg` is imported from there rather than copied.
 */

import { useEffect, useRef, useState } from 'react';

import { Badge, Skeleton, cn } from '@garh/ui';

import { fetchSheetContent, SHEET_KIND_LABELS, type Sheet } from './api';
import { assertRenderableSvg } from './SheetViewer';

export interface SheetThumbnailProps {
  projectId: string;
  sheet: Sheet;
  selected: boolean;
  onSelect: (sheetId: string) => void;
}

export function SheetThumbnail({
  projectId,
  sheet,
  selected,
  onSelect,
}: SheetThumbnailProps): JSX.Element {
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const cardRef = useRef<HTMLButtonElement | null>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const node = cardRef.current;
    if (!node || visible) return;
    // jsdom and older Safari have no IntersectionObserver. Treat its absence as
    // "everything is visible" rather than never loading a preview.
    if (typeof IntersectionObserver === 'undefined') {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) setVisible(true);
      },
      { rootMargin: '300px' },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [visible]);

  useEffect(() => {
    if (!visible) return;
    const controller = new AbortController();
    fetchSheetContent(projectId, sheet.id, controller.signal)
      .then((content) => {
        assertRenderableSvg(content.svg);
        setSvg(content.svg);
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => controller.abort();
  }, [projectId, sheet.id, visible]);

  const kindLabel = SHEET_KIND_LABELS[sheet.kind] ?? sheet.kind;

  return (
    <button
      ref={cardRef}
      type="button"
      onClick={() => onSelect(sheet.id)}
      aria-pressed={selected}
      data-testid="sheet-card"
      data-sheet-kind={sheet.kind}
      data-sheet-number={sheet.number ?? ''}
      className={cn(
        'group flex flex-col overflow-hidden rounded-md border bg-surface text-left transition',
        selected ? 'border-brand ring-1 ring-brand' : 'border-line hover:border-ink-muted',
      )}
    >
      <span className="relative block aspect-[297/210] w-full overflow-hidden bg-white">
        {svg ? (
          <span
            className="pointer-events-none absolute inset-0 flex items-center justify-center [&>svg]:h-full [&>svg]:w-full"
            // Same provenance and the same three checks as the full viewer.
            dangerouslySetInnerHTML={{ __html: svg }}
          />
        ) : failed ? (
          <span className="absolute inset-0 flex items-center justify-center px-3 text-center text-2xs text-ink-muted">
            Preview unavailable — the drawing is still downloadable.
          </span>
        ) : (
          <Skeleton className="absolute inset-0 h-full w-full" shape="block" />
        )}
      </span>
      <span className="flex items-center gap-2 border-t border-line px-2.5 py-2">
        <span className="font-mono text-2xs font-semibold text-ink">{sheet.number ?? '—'}</span>
        <span className="truncate text-xs text-ink">{sheet.title ?? kindLabel}</span>
        <span className="ml-auto flex items-center gap-1">
          {sheet.orphanedAnnotationCount > 0 ? (
            <Badge tone="warn" title="Notes on this sheet need re-attaching">
              {sheet.orphanedAnnotationCount}
            </Badge>
          ) : null}
          {sheet.scaleDenominator ? (
            <span className="font-mono text-2xs text-ink-muted">1:{sheet.scaleDenominator}</span>
          ) : null}
        </span>
      </span>
    </button>
  );
}

export default SheetThumbnail;
