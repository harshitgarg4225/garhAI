/**
 * CoPresenceOverlayUi — what the co-presence overlay actually looks like.
 *
 * Everything here renders inside `CanvasDomOverlay`'s SECOND React root, so the
 * rule from that file applies to every line below: **props and module stores
 * only, never React context.** No `useCanvasCore`, no `useToast`, no `Tooltip`
 * from the design system (it is context-backed) — the pin tooltip below is a
 * plain positioned div for exactly that reason.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * HOW POSITIONS ARE WRITTEN (§14)
 * ────────────────────────────────────────────────────────────────────────────
 * Not through React state. A pan commits the camera up to sixty times a second
 * and every pin's pixel position changes on each one; re-rendering this tree
 * per frame would put reconciliation inside the pointer path, which is the
 * thing `overlays/render/screenScale.ts` exists to avoid.
 *
 * So this file uses screenScale's trick, adapted to the DOM: React renders the
 * STRUCTURE (which pins exist, what they say, what colour they are), each item
 * carries its model coordinates in `data-x-mm` / `data-y-mm`, and one
 * subscription walks `container.children` on every camera commit writing
 * `transform` and `display`. Iterating the live children list rather than
 * keeping a registry of refs is deliberate and is screenScale's argument
 * verbatim: React 18 hands a callback ref `null` on detach without saying which
 * object it is detaching, so a ref registry leaks entries as pins churn, while
 * `children` is maintained by React's own reconciliation for free.
 *
 * React re-renders here only when the SET changes — a pin added, a comment
 * resolved, a collaborator's cursor frame arriving (≤10Hz, and ≤ a handful of
 * nodes). Never on a camera commit.
 */

import { useLayoutEffect, useRef, useState } from 'react';

import { Icon, cn } from '@garh/ui';

import { presencePaletteClasses } from '../../../components/PresenceChips';
import { pinExcerpt, type CommentPin } from '../../comments/anchor';
import type { PinPlacement } from '../../comments/pinStore';
import type { RemoteCursor } from '../../../stores/collab';
import type { OverlayPoint } from './overlayProjection';

/** Projects a model point to canvas pixels. Supplied by the R3F side. */
export type ProjectMm = (xMm: number, yMm: number) => OverlayPoint;

export interface CoPresenceOverlayUiProps {
  readonly cursors: readonly RemoteCursor[];
  readonly pins: readonly CommentPin[];
  readonly placement: PinPlacement;
  /** Subscribe to camera commits; returns an unsubscribe. rAF-coalesced. */
  readonly subscribeViewport: (listener: () => void) => () => void;
  readonly projectMm: ProjectMm;
  /** A click landed while armed: canvas-relative CSS pixels. */
  readonly onPlacementClick: (px: number, py: number) => void;
  readonly onCancelPlacement: () => void;
  readonly onPinClick: (commentId: string) => void;
}

// ---------------------------------------------------------------------------
// The imperative positioner
// ---------------------------------------------------------------------------

/**
 * Keep every direct child of a container positioned at its own model point.
 *
 * Each child must carry `data-x-mm` and `data-y-mm`. Children are translated,
 * never the container: the container is the canvas-sized coordinate space, and
 * scaling or moving it would move every child with it.
 *
 * `structureKey` is what makes this run after a React commit that changed the
 * list. Subscribing to the camera alone would leave a newly added pin at
 * `translate3d(0,0,0)` — the canvas's top-left corner — until the next time
 * somebody happened to pan.
 */
function useProjectedChildren(
  projectMm: ProjectMm,
  subscribeViewport: (listener: () => void) => () => void,
  structureKey: string,
): (node: HTMLDivElement | null) => void {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const projectRef = useRef(projectMm);
  projectRef.current = projectMm;

  const apply = useRef<() => void>(() => undefined);
  apply.current = (): void => {
    const container = containerRef.current;
    if (container === null) return;
    const children = container.children;
    // Indexed loop, no iterator allocation: this runs on every camera commit.
    // eslint-disable-next-line @typescript-eslint/prefer-for-of -- see above
    for (let i = 0; i < children.length; i++) {
      const child = children[i];
      if (!(child instanceof HTMLElement)) continue;
      const xMm = Number(child.dataset.xMm);
      const yMm = Number(child.dataset.yMm);
      if (!Number.isFinite(xMm) || !Number.isFinite(yMm)) continue;
      const point = projectRef.current(xMm, yMm);
      // `display` rather than `visibility`: an off-screen pin must not be
      // hit-testable, and a `visibility:hidden` element still occupies its box.
      child.style.display = point.onScreen ? '' : 'none';
      if (!point.onScreen) continue;
      child.style.transform = `translate3d(${String(point.x)}px, ${String(point.y)}px, 0)`;
    }
  };

  useLayoutEffect(() => {
    apply.current();
    return subscribeViewport(() => apply.current());
  }, [subscribeViewport, structureKey]);

  return (node) => {
    containerRef.current = node;
    if (node !== null) apply.current();
  };
}

// ---------------------------------------------------------------------------
// The overlay
// ---------------------------------------------------------------------------

export function CoPresenceOverlayUi({
  cursors,
  pins,
  placement,
  subscribeViewport,
  projectMm,
  onPlacementClick,
  onCancelPlacement,
  onPinClick,
}: CoPresenceOverlayUiProps): JSX.Element {
  const armed = placement.phase === 'armed';

  // One key per structural change. Cheap to build (a few dozen short strings at
  // most) and it is what re-runs the positioner after React commits a new list.
  const cursorKey = cursors.map((c) => `${c.userId}:${String(c.x)}:${String(c.y)}`).join('|');
  const pinKey = pins.map((p) => `${p.comment.id}:${String(p.number)}`).join('|');

  const cursorContainerRef = useProjectedChildren(projectMm, subscribeViewport, cursorKey);
  const pinContainerRef = useProjectedChildren(projectMm, subscribeViewport, pinKey);

  return (
    <>
      {/* ── placement capture ─────────────────────────────────────────────
          A full-canvas catcher, present ONLY while armed. It sits above the
          canvas, so the next left-click lands here instead of on a drawing
          tool, and `stopPropagation` keeps it from bubbling on to
          `useCanvasControls` on the way out.

          The middle button and the wheel are deliberately let through, so pan
          and zoom keep working while you look for the right spot — a placement
          mode that froze the view would force you to cancel, navigate and
          re-arm to pin something just off screen. */}
      {armed ? (
        <div
          data-testid="pin-placement-catcher"
          className="absolute inset-0 cursor-crosshair"
          style={{ pointerEvents: 'auto' }}
          onPointerDown={(event) => {
            if (event.button !== 0) return;
            event.stopPropagation();
          }}
          onPointerUp={(event) => {
            if (event.button !== 0) return;
            event.stopPropagation();
            const rect = event.currentTarget.getBoundingClientRect();
            onPlacementClick(event.clientX - rect.left, event.clientY - rect.top);
          }}
        />
      ) : null}

      {/* ── remote cursors ────────────────────────────────────────────────
          `pointer-events:none` on the container AND on every arrow: a cursor is
          a picture of where somebody else is, and if it could be clicked it
          would be stealing clicks meant for the wall underneath it. This is the
          strongest available form of "never a pick target" — stronger than the
          WebGL layers' `raycast={() => null}`, because these nodes are not in
          the scene graph the picker raycasts at all. */}
      <div ref={cursorContainerRef} className="pointer-events-none absolute inset-0">
        {cursors.map((cursor) => (
          <RemoteCursorMark key={cursor.userId} cursor={cursor} />
        ))}
      </div>

      {/* ── comment pins ──────────────────────────────────────────────────── */}
      <div ref={pinContainerRef} className="pointer-events-none absolute inset-0">
        {pins.map((pin) => (
          <CommentPinMark key={pin.comment.id} pin={pin} onClick={onPinClick} />
        ))}
      </div>

      {armed ? <PlacementBanner onCancel={onCancelPlacement} /> : null}
    </>
  );
}

// ---------------------------------------------------------------------------
// One remote cursor
// ---------------------------------------------------------------------------

/**
 * An arrow with a name tag, in the owner's presence colour.
 *
 * The arrow is an inline SVG rather than a glyph so its tip is exactly at the
 * element's origin — which is the pixel the positioner translates to, and
 * therefore the millimetre the person is actually pointing at. A centred glyph
 * would put their cursor half a label away from the thing they are describing.
 */
function RemoteCursorMark({ cursor }: { readonly cursor: RemoteCursor }): JSX.Element {
  const palette = presencePaletteClasses(cursor.userId);
  const name = cursor.name === '' ? 'A teammate' : cursor.name;
  return (
    <div
      data-x-mm={cursor.x}
      data-y-mm={cursor.y}
      // `will-change` is worth it here and nowhere else in the overlay: these
      // nodes move on their own schedule (network frames), not only with the
      // camera, so the compositor keeps them on their own layer.
      className="pointer-events-none absolute left-0 top-0 select-none will-change-transform"
      aria-hidden="true"
    >
      <svg width="18" height="18" viewBox="0 0 18 18" className="drop-shadow-sm">
        {/* Two paths: a light outline under a filled body, so the arrow stays
            readable over both the dark poché of a wall and the pale room wash.
            `currentColor` is not used — the palette pair is a background/ink
            token pair, and an arrow wants the SOLID one. */}
        <path
          d="M2 1 L2 14 L5.6 10.6 L8 15.6 L10.6 14.4 L8.2 9.6 L13 9.4 Z"
          className="fill-surface"
        />
        <path d="M3 3 L3 12 L5.8 9.4 L7.9 13.9 L9.3 13.2 L7.2 8.8 L11 8.6 Z" className="fill-ink" />
      </svg>
      <span
        className={cn(
          'absolute left-4 top-4 max-w-[10rem] truncate rounded-full px-1.5 py-0.5',
          'text-2xs font-semibold shadow-sm',
          palette,
        )}
      >
        {name}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// One comment pin
// ---------------------------------------------------------------------------

function CommentPinMark({
  pin,
  onClick,
}: {
  readonly pin: CommentPin;
  readonly onClick: (commentId: string) => void;
}): JSX.Element {
  const [hovered, setHovered] = useState(false);
  const resolved = pin.comment.resolved;
  const author = pin.comment.authorName === '' ? 'Someone' : pin.comment.authorName;
  const excerpt = pinExcerpt(pin.comment.body);

  return (
    <div
      data-x-mm={pin.anchor.x}
      data-y-mm={pin.anchor.y}
      className="pointer-events-none absolute left-0 top-0"
    >
      <button
        type="button"
        // The pin's own pointer events are the only ones this overlay claims
        // besides the placement catcher, and each is stopped before it can
        // bubble to `useCanvasControls`: without this, clicking a pin would
        // ALSO run whatever tool is active and, say, start a wall under it.
        onPointerDown={(event) => event.stopPropagation()}
        onPointerUp={(event) => event.stopPropagation()}
        onClick={(event) => {
          event.stopPropagation();
          onClick(pin.comment.id);
        }}
        onPointerEnter={() => setHovered(true)}
        onPointerLeave={() => setHovered(false)}
        onFocus={() => setHovered(true)}
        onBlur={() => setHovered(false)}
        aria-label={`Comment ${String(pin.number)} by ${author}: ${excerpt}`}
        className={cn(
          'garh-focus-ring pointer-events-auto flex items-center justify-center rounded-full',
          'font-semibold shadow-sm ring-2 ring-surface transition-transform',
          // The tip of a pin is its bottom-left corner in this treatment, so
          // the badge is nudged up and right to sit ON the anchored point
          // rather than beside it.
          '-translate-x-1 -translate-y-full',
          resolved
            ? 'h-4 w-4 bg-neutral-soft text-2xs text-neutral-ink opacity-60'
            : 'h-6 w-6 bg-brand-soft text-2xs text-brand-ink hover:scale-110',
        )}
      >
        {pin.number}
      </button>

      {hovered ? (
        <div
          role="tooltip"
          className={cn(
            'pointer-events-none absolute left-4 top-0 z-10 w-56 rounded-md border border-line',
            'bg-surface px-2 py-1.5 text-left shadow-md',
          )}
        >
          <p className="truncate text-2xs font-semibold text-ink">{author}</p>
          <p className="mt-0.5 line-clamp-2 text-2xs leading-4 text-ink-muted">{excerpt}</p>
          {resolved ? <p className="mt-1 text-2xs text-pass-ink">Resolved</p> : null}
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// The mode banner
// ---------------------------------------------------------------------------

/**
 * "You are in a mode" made obvious, in the same idiom as the tool and furniture
 * HUDs: a single strip along the top of the canvas naming the mode, what to do
 * next, and how to get out. A crosshair cursor alone is not enough — it is easy
 * to miss and impossible to discover the exit from.
 */
function PlacementBanner({ onCancel }: { readonly onCancel: () => void }): JSX.Element {
  return (
    <div className="pointer-events-none absolute inset-x-0 top-2 flex justify-center">
      <div
        role="status"
        className={cn(
          'pointer-events-auto flex items-center gap-2 rounded-full border border-brand/40',
          'bg-surface px-3 py-1.5 text-xs text-ink shadow-md',
        )}
      >
        <Icon name="pin" size={14} className="text-brand" />
        <span>Click the plan to pin your comment</span>
        <kbd className="rounded border border-line px-1 text-2xs">Esc</kbd>
        <button
          type="button"
          onClick={onCancel}
          className="garh-focus-ring rounded px-1 text-2xs font-semibold text-ink-muted hover:text-ink"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
