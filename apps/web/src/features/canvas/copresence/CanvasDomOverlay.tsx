/**
 * CanvasDomOverlay — DOM chrome, drawn over the canvas, mounted from inside it.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY THIS FILE EXISTS AT ALL
 * ════════════════════════════════════════════════════════════════════════════
 * `CanvasRoot` already has the right answer for DOM over the drawing: its
 * `overlay` prop, rendered outside the WebGL context, "on purpose — text in the
 * DOM is accessible, selectable and free". Everything in this folder would
 * rather live there.
 *
 * It cannot. `overlay` is supplied by `PlanPage`, and the co-presence layers
 * are mounted from `PlanScene`, which is INSIDE the `<Canvas>`. That boundary
 * is not a style choice: react-three-fiber reconciles its children with its own
 * renderer, whose host config creates three.js objects. `ReactDOM.createPortal`
 * does not help — React reconciles a portal's children with the CURRENT
 * renderer and merely redirects where they are attached, so a `<div>` inside
 * the R3F tree is asked of three.js, which has no such thing.
 *
 * The way out is the one `@react-three/drei`'s own `<Html>` takes, and this is
 * a deliberately smaller copy of it: create a plain DOM node, append it beside
 * the canvas, and drive it with a SECOND React root. Not `<Html>` itself
 * because `<Html>` is a per-point component — it positions its container from
 * an object's projection, one React root per instance — and this overlay is one
 * full-canvas surface holding many points that position themselves.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT THE SECOND ROOT COSTS, AND THE RULE THAT FOLLOWS
 * ════════════════════════════════════════════════════════════════════════════
 * React context does NOT cross a root boundary. Nothing rendered through here
 * may call `useCanvasCore`, `useToast`, the theme context or any other
 * provider-backed hook — it would throw, or worse, silently read a default.
 *
 * So the contract is: **everything crosses as props.** The R3F-side component
 * does all the hook work and hands down plain values and plain callbacks
 * (functions close over `core` perfectly well; only context is blocked).
 * Zustand stores are module-scoped external stores and work in either root,
 * which is what makes the panel ↔ canvas bridge possible at all.
 *
 * Tailwind classes still apply — same document, same stylesheet — so the
 * overlay can use the design tokens and the presence palette directly, which
 * is the whole reason cursors are DOM here rather than WebGL: the per-user
 * colour is a token class pair owned by `PresenceChips`, and reproducing it as
 * three.js `Color`s would be the second palette that file's docblock forbids.
 */

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { useThree } from '@react-three/fiber';

export interface CanvasDomOverlayProps {
  /**
   * The overlay tree. Rendered in a separate React root — see the rule above:
   * props and stores only, never context.
   */
  readonly children: ReactNode;
}

/**
 * Mount `children` into a full-bleed div above the canvas.
 *
 * Returns `null` into the R3F tree: this component contributes no geometry, and
 * a `<group>` with nothing in it would still be one more object for the scene
 * graph to walk on every traverse.
 */
export function CanvasDomOverlay({ children }: CanvasDomOverlayProps): null {
  const gl = useThree((state) => state.gl);
  const [host, setHost] = useState<HTMLDivElement | null>(null);
  const rootRef = useRef<Root | null>(null);

  // ── the DOM node ─────────────────────────────────────────────────────────
  // Appended to the canvas's own parent, exactly where drei puts its overlays.
  // R3F wraps the canvas in two divs; the outer one carries `position:relative`
  // (plus the `position:absolute; inset:0` CanvasRoot passes through), so an
  // absolutely positioned child of the inner one resolves against the canvas's
  // box. `pointer-events:none` is the default because this surface sits on top
  // of every drawing tool: only the specific things that must be clickable turn
  // it back on, and a full-canvas div that swallows pointer events would break
  // every tool in the product in a way no test would notice.
  useEffect(() => {
    const parent = gl.domElement.parentElement;
    if (parent === null) return undefined;
    const el = document.createElement('div');
    el.dataset.garhOverlay = 'copresence';
    el.style.cssText =
      'position:absolute;top:0;left:0;width:100%;height:100%;overflow:hidden;pointer-events:none;';
    parent.appendChild(el);
    setHost(el);
    return () => {
      setHost(null);
      el.remove();
    };
  }, [gl]);

  // ── the React root ───────────────────────────────────────────────────────
  useEffect(() => {
    if (host === null) return undefined;
    const root = createRoot(host);
    rootRef.current = root;
    return () => {
      rootRef.current = null;
      // DEFERRED, and this is not superstition: React refuses to unmount a root
      // synchronously while another root is rendering, and this cleanup runs
      // inside the commit of the R3F root that owns this component. Unmounting
      // here logs a warning and can drop the teardown; a task boundary puts it
      // safely after the commit. `root.unmount()` on an already-detached node
      // is a no-op, so the ordering against the div's removal does not matter.
      setTimeout(() => root.unmount(), 0);
    };
  }, [host]);

  // Re-render the overlay whenever this component renders. No dependency array
  // on purpose: `children` is a fresh element tree every render, so any list
  // would either be a lie or `[children]`, which is the same thing written
  // less honestly. React's own reconciliation makes the repeat cheap.
  useEffect(() => {
    rootRef.current?.render(children);
  });

  return null;
}
