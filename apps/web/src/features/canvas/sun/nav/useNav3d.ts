/**
 * useNav3d.ts — the 3D tab's navigation layer. First real exerciser of the
 * rig's orbit helpers (inherited fact 2): `orbitByPx`, `orbitEyeMm` (via
 * `orbitOps`), `fitOrbitToBbox` (via `viewport.fitBbox`), `mmPerPxAtDistance`
 * (via `viewport.mmPerPx`, which scales the pan gesture).
 *
 * CONTRACT WITH `useCanvasControls`: mount this INSTEAD OF the core hook's
 * built-in navigation while the canvas is in 3D — pass
 * `navigation: viewMode === '2d'` to `useCanvasControls` and hand this hook
 * the same wrapper element. Running both would double-handle the wheel (a
 * dolly per hook per notch). This hook takes no pointer events away from
 * tools: Phase 5's 3D view has no drawing tools, so left-drag is free to
 * orbit; when 3D tools arrive, the same `enabled` flag hands left-drag back.
 *
 * GESTURES (superset of the core hook's 3D set, so muscle memory holds):
 *   left-drag / middle-drag   orbit (orbit mode) · look around (walk mode)
 *   shift + drag              pan the orbit target in the ground plane
 *   wheel                     dolly TO THE CURSOR (orbit) · step forward (walk)
 *   double-click              fit the building
 *   W A S D / arrows          walk (walk mode only; Shift strides)
 *
 * Zoom-to-cursor anchors on the real thing under the pointer — one raycast
 * through the shared picker per wheel notch (`core.pick`), falling back to
 * the reference-plane point, then to a plain centre dolly above the horizon.
 *
 * KEYMAP COLLISION, stated plainly: in walk mode W/A/S/D are movement, and
 * the 2D keymap binds those same letters to tools (W wall, D door, S stair).
 * This hook preventDefaults but cannot stop other listeners — the page that
 * mounts it must scope the tool shortcuts to 2D (`keyboardEnabled` /
 * `viewMode` in the ui store) or walking would switch tools mid-stride.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  ndcFromPixel,
  orbitByPx,
  wheelZoomFactor,
  type CanvasCore,
  type PtF3,
} from '../../core';
import { useModelStore } from '../../../../stores/model';
import { buildingExtentOf, type BuildingExtent } from '../buildingBbox';
import {
  dollyOrbitAboutAnchor,
  enterWalkOrbit,
  walkStep,
  walkTurn,
  WALK_RUN_FACTOR,
  WALK_SPEED_MM_PER_S,
} from './orbitOps';

export type NavMode = 'orbit' | 'walk';

export interface Nav3dOptions {
  core: CanvasCore;
  /** Attach only while the canvas is actually in 3D. */
  enabled?: boolean | undefined;
  /**
   * Box the fit action frames. Defaults to the whole building from the model
   * store. Return null for "nothing to fit" — the action then no-ops honestly.
   */
  getFitExtent?: (() => BuildingExtent | null) | undefined;
}

export interface Nav3dApi {
  readonly navMode: NavMode;
  readonly setNavMode: (mode: NavMode) => void;
  /** Frame the whole building. Returns false when there is nothing to frame. */
  readonly fitToBuilding: () => boolean;
}

const WALK_KEYS: Readonly<Record<string, 'f' | 'b' | 'l' | 'r'>> = {
  KeyW: 'f',
  ArrowUp: 'f',
  KeyS: 'b',
  ArrowDown: 'b',
  KeyA: 'l',
  ArrowLeft: 'l',
  KeyD: 'r',
  ArrowRight: 'r',
};

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return (
    tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable === true
  );
}

function defaultFitExtent(): BuildingExtent | null {
  return buildingExtentOf(useModelStore.getState().doc.house);
}

export function useNav3d(element: HTMLElement | null, options: Nav3dOptions): Nav3dApi {
  const [navMode, setNavModeState] = useState<NavMode>('orbit');

  const latest = useRef(options);
  latest.current = options;
  const navModeRef = useRef(navMode);
  navModeRef.current = navMode;

  const setNavMode = useCallback((mode: NavMode) => {
    if (navModeRef.current === mode) return;
    const viewport = latest.current.core.viewport;
    if (mode === 'walk') {
      viewport.setOrbit(enterWalkOrbit(viewport.orbit, viewport.planeElevationMm));
    }
    // Leaving walk keeps the camera where it stands; the next orbit drag
    // simply pivots about the short look-ahead target. No jump cut.
    navModeRef.current = mode;
    setNavModeState(mode);
  }, []);

  const fitToBuilding = useCallback((): boolean => {
    const extent = (latest.current.getFitExtent ?? defaultFitExtent)();
    if (extent === null) return false;
    const viewport = latest.current.core.viewport;
    viewport.setFitHeightMm(extent.heightMm);
    viewport.fitBbox(extent.box);
    navModeRef.current = 'orbit'; // a fit is an orbit-framing statement
    setNavModeState('orbit');
    return true;
  }, []);

  useEffect(() => {
    if (element === null || options.enabled === false) return;

    const core = latest.current.core;
    const viewport = core.viewport;

    let rect = element.getBoundingClientRect();
    const refreshRect = (): void => {
      rect = element.getBoundingClientRect();
    };
    const resizeObserver =
      typeof ResizeObserver === 'function' ? new ResizeObserver(refreshRect) : null;
    resizeObserver?.observe(element);

    // ── drag: orbit / look / pan ─────────────────────────────────────────
    let dragPointerId: number | null = null;
    let lastX = 0;
    let lastY = 0;

    const onPointerDown = (event: PointerEvent): void => {
      if (event.button !== 0 && event.button !== 1) return;
      refreshRect();
      dragPointerId = event.pointerId;
      lastX = event.clientX;
      lastY = event.clientY;
      element.setPointerCapture(event.pointerId);
      element.style.cursor = 'grabbing';
      event.preventDefault();
    };

    const onPointerMove = (event: PointerEvent): void => {
      if (dragPointerId !== event.pointerId) return;
      const dx = event.clientX - lastX;
      const dy = event.clientY - lastY;
      lastX = event.clientX;
      lastY = event.clientY;
      if (event.shiftKey) {
        panGround(core, dx, dy);
        return;
      }
      if (navModeRef.current === 'walk') {
        viewport.setOrbit(walkTurn(viewport.orbit, dx, dy));
      } else {
        viewport.setOrbit(orbitByPx(viewport.orbit, dx, dy));
      }
    };

    const endDrag = (event: PointerEvent): void => {
      if (dragPointerId !== event.pointerId) return;
      dragPointerId = null;
      element.style.cursor = '';
      if (element.hasPointerCapture(event.pointerId)) {
        element.releasePointerCapture(event.pointerId);
      }
    };

    // ── wheel: dolly to cursor / step ────────────────────────────────────
    const onWheel = (event: WheelEvent): void => {
      event.preventDefault();
      refreshRect();
      const factor = wheelZoomFactor(event.deltaY, event.deltaMode);
      if (navModeRef.current === 'walk') {
        // Wheel walks: a notch is a step, in the direction you face.
        viewport.setOrbit(walkStep(viewport.orbit, -Math.sign(event.deltaY) * 600, 0));
        return;
      }
      const pixel = { x: event.clientX - rect.left, y: event.clientY - rect.top };
      const ndc = ndcFromPixel(pixel, { width: rect.width, height: rect.height });
      const hit = core.pick(ndc);
      const anchor: PtF3 | null =
        hit.pointMm !== null ? { x: hit.pointMm.x, y: hit.pointMm.y, z: hit.elevationMm } : null;
      if (anchor === null) {
        // Pointer above the horizon: dolly about the target, honestly centred.
        viewport.setOrbit(
          dollyOrbitAboutAnchor(viewport.orbit, factor, viewport.orbit.targetMm),
        );
        return;
      }
      viewport.setOrbit(dollyOrbitAboutAnchor(viewport.orbit, factor, anchor));
    };

    const onDoubleClick = (event: MouseEvent): void => {
      event.preventDefault();
      fitToBuilding();
    };

    // ── walk keys, stepped by rAF so held keys glide ─────────────────────
    const held = new Set<'f' | 'b' | 'l' | 'r'>();
    let running = false;
    let walkFrame = 0;
    let lastStepAt = 0;

    const stepLoop = (now: number): void => {
      walkFrame = 0;
      if (held.size === 0 || navModeRef.current !== 'walk') return;
      const dtS = Math.min(0.1, (now - lastStepAt) / 1000);
      lastStepAt = now;
      const speed = WALK_SPEED_MM_PER_S * (running ? WALK_RUN_FACTOR : 1);
      const forward = (held.has('f') ? 1 : 0) - (held.has('b') ? 1 : 0);
      const right = (held.has('r') ? 1 : 0) - (held.has('l') ? 1 : 0);
      if (forward !== 0 || right !== 0) {
        viewport.setOrbit(walkStep(viewport.orbit, forward * speed * dtS, right * speed * dtS));
      }
      walkFrame = requestAnimationFrame(stepLoop);
    };

    const onKeyDown = (event: KeyboardEvent): void => {
      if (navModeRef.current !== 'walk' || isEditableTarget(event.target)) return;
      running = event.shiftKey;
      const dir = WALK_KEYS[event.code];
      if (dir === undefined) return;
      event.preventDefault();
      if (!held.has(dir)) {
        held.add(dir);
        if (walkFrame === 0) {
          lastStepAt = performance.now();
          walkFrame = requestAnimationFrame(stepLoop);
        }
      }
    };

    const onKeyUp = (event: KeyboardEvent): void => {
      running = event.shiftKey;
      const dir = WALK_KEYS[event.code];
      if (dir !== undefined) held.delete(dir);
    };

    const onBlur = (): void => held.clear();

    element.addEventListener('pointerdown', onPointerDown);
    element.addEventListener('pointermove', onPointerMove);
    element.addEventListener('pointerup', endDrag);
    element.addEventListener('pointercancel', endDrag);
    element.addEventListener('dblclick', onDoubleClick);
    element.addEventListener('wheel', onWheel, { passive: false });
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);
    window.addEventListener('blur', onBlur);

    return () => {
      if (walkFrame !== 0) cancelAnimationFrame(walkFrame);
      resizeObserver?.disconnect();
      element.removeEventListener('pointerdown', onPointerDown);
      element.removeEventListener('pointermove', onPointerMove);
      element.removeEventListener('pointerup', endDrag);
      element.removeEventListener('pointercancel', endDrag);
      element.removeEventListener('dblclick', onDoubleClick);
      element.removeEventListener('wheel', onWheel);
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
      window.removeEventListener('blur', onBlur);
      element.style.cursor = '';
    };
    // Options are read through `latest`; only the element and the switch re-attach.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [element, options.enabled, fitToBuilding]);

  return useMemo(
    () => ({ navMode, setNavMode, fitToBuilding }),
    [navMode, setNavMode, fitToBuilding],
  );
}

/**
 * Shift-drag pan: slide the orbit target in the ground plane along the
 * camera's screen axes. Same maths as the core hook's 3D pan, restated here
 * because this hook replaces the core's navigation entirely in 3D.
 */
function panGround(core: CanvasCore, dxPx: number, dyPx: number): void {
  const viewport = core.viewport;
  const mmPerPx = viewport.mmPerPx; // rig helper: perspective zoom equivalence
  const a = (viewport.orbit.azimuthDeg * Math.PI) / 180;
  const rightX = -Math.sin(a);
  const rightY = Math.cos(a);
  const forwardX = Math.cos(a);
  const forwardY = Math.sin(a);
  const target = viewport.orbit.targetMm;
  viewport.setOrbit({
    ...viewport.orbit,
    targetMm: {
      x: target.x - (dxPx * rightX + dyPx * forwardX) * mmPerPx,
      y: target.y - (dxPx * rightY + dyPx * forwardY) * mmPerPx,
      z: target.z,
    },
  });
}
