/**
 * The §5.5 options surface, as the Plan tab hosts it.
 *
 * The dashboard's stage map has always said options live on the Plan tab
 * (`STAGE_ROUTE.options === 'plan'`); this overlay is that mount. It sits OVER
 * the canvas rather than beside it because reviewing options is a mode, not a
 * rail: the architect compares cards, applies one, and returns to the drawing —
 * which is already updating underneath, since apply dispatches ops through the
 * model store like every other edit (one undo step, `source: 'solver'`).
 *
 * Everything inside the panel — generate, the theater, cards, apply/compare —
 * is `OptionsPanel`'s; this file owns only the overlay chrome. Escape and the
 * close button both leave the plan exactly as it is.
 */

import { useEffect } from 'react';

import { IconButton } from '@garh/ui';

import type { PtMm } from './types';
import { OptionsPanel } from './OptionsPanel';

export interface OptionsOverlayProps {
  readonly projectId: string;
  readonly plotOutline?: readonly PtMm[] | undefined;
  readonly briefReady?: boolean | undefined;
  readonly onClose: () => void;
}

export function OptionsOverlay({
  projectId,
  plotOutline,
  briefReady,
  onClose,
}: OptionsOverlayProps): JSX.Element {
  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="false"
      aria-label="Plan options"
      className="absolute inset-0 z-30 flex flex-col overflow-hidden bg-canvas/95 backdrop-blur-sm"
    >
      <div className="flex h-topbar shrink-0 items-center gap-3 border-b border-line bg-surface px-4">
        <h2 className="text-sm font-semibold text-ink">Plan options</h2>
        <p className="hidden text-xs text-ink-muted sm:block">
          Apply one to put it on the canvas — it lands as a single undo step.
        </p>
        <IconButton
          icon="x"
          label="Close options"
          onClick={onClose}
          className="ml-auto"
        />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <OptionsPanel projectId={projectId} plotOutline={plotOutline} briefReady={briefReady} />
      </div>
    </div>
  );
}
