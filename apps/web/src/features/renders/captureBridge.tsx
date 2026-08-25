/**
 * captureBridge.tsx — how the renders feature reaches the ONE live renderer.
 *
 * `RenderCaptureBridge` mounts inside the product's single `<Canvas>` (PlanPage
 * adds it next to the shared layers) and registers the live `WebGLRenderer` +
 * `Scene` into a module-level registry. Nothing here renders DOM, causes React
 * work, or creates GL state — it is a handle, published on mount and withdrawn
 * on unmount.
 *
 * Everything outside the canvas (the Renders tab, the launcher panel) asks
 * `captureSource()` and treats `null` honestly: "open the 3D view first". That
 * is the §12-compatible alternative to a second canvas, which is forbidden.
 */

import { useEffect } from 'react';
import { useThree } from '@react-three/fiber';
import type { Camera, Scene, WebGLRenderer } from 'three';

export interface CaptureSource {
  readonly gl: WebGLRenderer;
  readonly scene: Scene;
  /** The camera the user is looking through right now (the rig's live one). */
  readonly camera: Camera;
}

type Listener = () => void;

let current: CaptureSource | null = null;
const listeners = new Set<Listener>();

function publish(next: CaptureSource | null): void {
  current = next;
  for (const listener of listeners) listener();
}

/** The live renderer handle, or null when no canvas is mounted. */
export function captureSource(): CaptureSource | null {
  return current;
}

/** Subscribe to availability changes (for `useSyncExternalStore`). */
export function subscribeCaptureSource(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Mount inside the `<Canvas>`. Renders nothing; registers the live renderer.
 */
export function RenderCaptureBridge(): null {
  const gl = useThree((s) => s.gl);
  const scene = useThree((s) => s.scene);
  const camera = useThree((s) => s.camera);

  useEffect(() => {
    publish({ gl, scene, camera });
    return () => {
      // Only clear if we are still the registered source (a remounted canvas
      // may have published its own handle before this cleanup ran).
      if (current !== null && current.gl === gl) publish(null);
    };
  }, [gl, scene, camera]);

  return null;
}
