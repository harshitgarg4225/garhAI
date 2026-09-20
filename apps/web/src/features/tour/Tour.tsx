/**
 * Tour — the six-step first-run walkthrough, one step at a time.
 *
 * Presentational: it takes the step index, the anchor's rectangle and four
 * callbacks. `ProjectTour` owns the store, the router and the auto-start.
 *
 * Accessibility is the whole design, not a layer on it:
 *   - a NON-MODAL `role="dialog"` (no `aria-modal`, no focus trap), labelled by
 *     the step title and described by its body, portalled to <body> so no
 *     `overflow-hidden` clips it;
 *   - focus moves to the card when the tour opens and is returned on close, but
 *     Tab is free to walk out of it into the app underneath — the card dims the
 *     app, it does not own it;
 *   - Escape skips the tour; ← / → (and Enter) move between steps, so a keyboard
 *     user needs no mouse; every key has a visible button;
 *   - the highlight around the anchor is decoration (`aria-hidden`); the card
 *     says "Step 2 of 6" in text, and `aria-live` announces each step change.
 *
 * `onFocusWithinChange` reports whether focus is inside the card, because the
 * shell uses it to decide who owns the keyboard. See `ProjectTour`.
 */

import { useEffect, useId, useRef, type JSX } from 'react';
import { createPortal } from 'react-dom';

import { Button, cn, useOnEscape } from '@garh/ui';

import { TOUR_STEPS, TOUR_STEP_COUNT, clampStep } from './steps';
import type { AnchorRect } from './useTourAnchor';

export interface TourProps {
  readonly open: boolean;
  /** Zero-based step index; clamped. */
  readonly step: number;
  /** Where the current step's anchor is, or null to centre the card. */
  readonly anchor: AnchorRect | null;
  readonly onStepChange: (step: number) => void;
  /** Escape, the Skip button, or the × — the tour is marked done either way. */
  readonly onSkip: () => void;
  /** Finish on the last step — also marked done. */
  readonly onFinish: () => void;
  /**
   * Focus entered (true) or left (false) the card.
   *
   * The card's arrow keys and the canvas's tool shortcuts are both live while
   * the tour is up, so something has to say which of them a keystroke means.
   * Focus is that something, and this is how the shell hears about it.
   */
  readonly onFocusWithinChange?: ((within: boolean) => void) | undefined;
}

const HIGHLIGHT_PAD = 6;
const CARD_WIDTH = 360;
const CARD_GAP = 12;

/** Card position: under the anchor, or above it when there is no room, clamped. */
export function placeCard(
  anchor: AnchorRect | null,
  viewport: { width: number; height: number },
  cardHeight: number,
): { top: number; left: number } | null {
  if (anchor === null) return null;
  const below = anchor.top + anchor.height + CARD_GAP;
  const above = anchor.top - CARD_GAP - cardHeight;
  const top = below + cardHeight <= viewport.height || above < 0 ? below : above;
  const left = Math.min(
    Math.max(CARD_GAP, anchor.left),
    Math.max(CARD_GAP, viewport.width - CARD_WIDTH - CARD_GAP),
  );
  return { top: Math.max(CARD_GAP, top), left };
}

export function Tour({
  open,
  step,
  anchor,
  onStepChange,
  onSkip,
  onFinish,
  onFocusWithinChange,
}: TourProps): JSX.Element | null {
  const cardRef = useRef<HTMLDivElement>(null);
  const restoreTo = useRef<HTMLElement | null>(null);
  const base = useId();
  const index = clampStep(step);
  const current = TOUR_STEPS[index];
  const last = index === TOUR_STEP_COUNT - 1;

  // Focus in, focus back — the useful half of a focus trap, without the half
  // that contradicts a non-modal dialog. `useFocusTrap` (which `Dialog` still
  // uses, correctly, because a Dialog IS modal) also cycles Tab inside the
  // container; doing that here would tell a keyboard user the app is
  // unreachable while the screen plainly shows it is.
  useEffect(() => {
    if (!open) return undefined;
    const node = cardRef.current;
    if (node === null) return undefined;
    restoreTo.current = document.activeElement as HTMLElement | null;
    node.focus({ preventScroll: true });
    return () => {
      restoreTo.current?.focus({ preventScroll: true });
      restoreTo.current = null;
    };
  }, [open]);

  useOnEscape(open, onSkip);

  // ← / → / Enter inside the card. Handled on the card, not on document: the
  // trap keeps focus inside it, and an Enter on the Back button must stay Back.
  useEffect(() => {
    if (!open) return undefined;
    const node = cardRef.current;
    if (node === null) return undefined;
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'ArrowRight') {
        event.preventDefault();
        if (last) onFinish();
        else onStepChange(index + 1);
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault();
        if (index > 0) onStepChange(index - 1);
      } else if (event.key === 'Enter' && event.target === node) {
        event.preventDefault();
        if (last) onFinish();
        else onStepChange(index + 1);
      }
    };
    node.addEventListener('keydown', onKeyDown);
    return () => node.removeEventListener('keydown', onKeyDown);
  }, [open, index, last, onStepChange, onFinish]);

  if (!open || current === undefined) return null;
  if (typeof document === 'undefined') return null;

  const viewport = { width: window.innerWidth || 1024, height: window.innerHeight || 768 };
  const placed = placeCard(anchor, viewport, 220);
  const titleId = `${base}-title`;
  const bodyId = `${base}-body`;

  return createPortal(
    <div className="pointer-events-none fixed inset-0 z-[70]" data-testid="tour">
      {/* The dim layer dims and nothing else: `pointer-events-none`, so the app
          underneath stays usable while the tour is up. It used to swallow every
          click, which is what broke the @smoke journey in CI run 91 — Playwright
          retried the plot button 33 times against this div and timed out, and an
          architect would have been just as stuck on their first project. A stray
          click still does not end the tour: Skip and Escape are explicit, and the
          card below re-enables pointer events for its own buttons. */}
      <div className="absolute inset-0 bg-ink/40" aria-hidden="true" />
      {anchor === null ? null : (
        <div
          aria-hidden="true"
          data-testid="tour-highlight"
          className="pointer-events-none absolute rounded-lg ring-4 ring-brand shadow-[0_0_0_9999px_rgba(0,0,0,0.0)]"
          style={{
            top: anchor.top - HIGHLIGHT_PAD,
            left: anchor.left - HIGHLIGHT_PAD,
            width: anchor.width + HIGHLIGHT_PAD * 2,
            height: anchor.height + HIGHLIGHT_PAD * 2,
          }}
        />
      )}
      <div
        ref={cardRef}
        role="dialog"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        tabIndex={-1}
        onFocus={() => onFocusWithinChange?.(true)}
        onBlur={(event) => {
          // React's onBlur is focusout, so it fires when focus moves between
          // the card's own buttons too. Only a target OUTSIDE the card means
          // the architect has gone back to the app.
          if (!event.currentTarget.contains(event.relatedTarget)) onFocusWithinChange?.(false);
        }}
        data-testid="tour-card"
        data-step={current.id}
        className={cn(
          'pointer-events-auto absolute flex w-[360px] max-w-[calc(100vw-24px)] flex-col gap-3 rounded-lg border border-line bg-surface p-4 shadow-xl outline-none',
          placed === null && 'left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2',
        )}
        style={placed === null ? undefined : { top: placed.top, left: placed.left }}
      >
        <div className="flex items-start justify-between gap-3">
          <p
            className="text-xs font-medium uppercase tracking-wide text-ink-muted"
            aria-live="polite"
          >
            Step {index + 1} of {TOUR_STEP_COUNT}
          </p>
          <button
            type="button"
            onClick={onSkip}
            className="garh-focus-ring rounded-md px-2 py-0.5 text-xs text-ink-muted hover:text-ink"
            data-testid="tour-skip"
          >
            Skip tour
          </button>
        </div>
        <h2 id={titleId} className="text-base font-semibold text-ink">
          {current.title}
        </h2>
        <p id={bodyId} className="text-sm leading-6 text-ink-muted">
          {current.body}
        </p>
        <ol className="flex items-center gap-1.5" aria-hidden="true">
          {TOUR_STEPS.map((s, i) => (
            <li
              key={s.id}
              className={cn(
                'h-1.5 w-1.5 rounded-full',
                i === index ? 'bg-brand' : 'bg-line-strong',
              )}
            />
          ))}
        </ol>
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs text-ink-subtle">← → to move · Esc to skip</span>
          <span className="flex gap-2">
            <Button
              size="sm"
              onClick={() => onStepChange(index - 1)}
              disabled={index === 0}
              data-testid="tour-back"
            >
              Back
            </Button>
            {last ? (
              <Button size="sm" variant="primary" onClick={onFinish} data-testid="tour-finish">
                Finish
              </Button>
            ) : (
              <Button
                size="sm"
                variant="primary"
                onClick={() => onStepChange(index + 1)}
                data-testid="tour-next"
              >
                Next
              </Button>
            )}
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
