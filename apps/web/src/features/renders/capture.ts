/**
 * capture.ts — §9's capture set, taken from the LIVE renderer.
 *
 * "Client captures viewport + depth (R3F depth pass) + edges (Sobel on
 * normals/depth) and uploads with the job."
 *
 * THE ONE RULE (inherited from Phase 4/5): there is exactly one `<Canvas>` in
 * the product. This module never creates a second one — it borrows the live
 * `WebGLRenderer` and the live scene graph (handed over by
 * `RenderCaptureBridge` in `captureBridge.tsx`) and renders one extra frame
 * into an offscreen `WebGLRenderTarget`. The user's camera, the viewport
 * controller and the demand frame-loop are untouched; the only 2D canvas here
 * is a plain DOM `<canvas>` used to encode pixels as PNG, which owns no GL
 * context at all.
 *
 * Three images per shot, at the requested output size so ControlNet's maps
 * align pixel-for-pixel:
 *
 *   viewport  the scene as rendered, alpha-flattened onto paper white
 *   depth     MeshDepthMaterial override, RGBA-packed, linearised, near=bright
 *             (the MiDaS/ControlNet-depth convention)
 *   edges     Sobel over the linearised depth — written by hand below, no new
 *             dependency — dark lines on a white background (the exact
 *             convention `services/render/mock.py` multiplies back in)
 *
 * Everything returns base64 PNG (no `data:` prefix) — the wire shape
 * `RenderInputs` in `garh_api/schemas/jobs.py` expects.
 */

import {
  Color,
  MeshDepthMaterial,
  NearestFilter,
  RGBADepthPacking,
  WebGLRenderTarget,
  type Camera,
  type PerspectiveCamera,
  type Scene,
  type WebGLRenderer,
} from 'three';

export interface CaptureSet {
  /** base64 PNG, no data: prefix. */
  readonly viewportPng: string;
  readonly depthPng: string;
  readonly edgesPng: string;
}

export interface CaptureSize {
  readonly width: number;
  readonly height: number;
}

/** Paper the viewport is flattened onto — the canvas clears to transparent. */
const BACKGROUND = { r: 238, g: 242, b: 247 };

/** Sobel gain: how strong a depth step must be to draw a line. */
const EDGE_GAIN = 6;

/**
 * Take the full §9 capture set through `camera`. Restores every piece of
 * renderer and scene state it touches, and asks for nothing from React — this
 * is plain Three.js so it can run mid-event-handler.
 */
export function captureSet(
  gl: WebGLRenderer,
  scene: Scene,
  camera: Camera,
  size: CaptureSize,
): CaptureSet {
  const { width, height } = size;
  const aspectCamera = camera as PerspectiveCamera;
  const hadAspect = typeof aspectCamera.aspect === 'number';
  const previousAspect = hadAspect ? aspectCamera.aspect : 1;
  if (hadAspect) {
    aspectCamera.aspect = width / height;
    aspectCamera.updateProjectionMatrix();
  }

  const target = new WebGLRenderTarget(width, height, {
    // Nearest: these pixels are read back and encoded, never sampled.
    minFilter: NearestFilter,
    magFilter: NearestFilter,
    depthBuffer: true,
    stencilBuffer: false,
  });

  const prevTarget = gl.getRenderTarget();
  const prevOverride = scene.overrideMaterial;
  const prevBackground = scene.background;
  const prevAutoClear = gl.autoClear;

  try {
    gl.autoClear = true;

    // ── 1. colour pass ───────────────────────────────────────────────────
    scene.overrideMaterial = null;
    scene.background = null;
    gl.setRenderTarget(target);
    gl.clear();
    gl.render(scene, camera);
    const colour = new Uint8Array(width * height * 4);
    gl.readRenderTargetPixels(target, 0, 0, width, height, colour);

    // ── 2. depth pass (the "R3F depth pass": one override render) ───────
    const depthMaterial = new MeshDepthMaterial({ depthPacking: RGBADepthPacking });
    scene.overrideMaterial = depthMaterial;
    scene.background = new Color(1, 1, 1); // far plane where nothing draws
    gl.setRenderTarget(target);
    gl.clear();
    gl.render(scene, camera);
    const packed = new Uint8Array(width * height * 4);
    gl.readRenderTargetPixels(target, 0, 0, width, height, packed);
    depthMaterial.dispose();

    const depth = unpackDepth(packed, width, height, camera);
    const edges = sobelEdges(depth, width, height);

    return {
      viewportPng: encodeColourPng(colour, width, height),
      depthPng: encodeGrayPng(depthToBrightness(depth), width, height),
      edgesPng: encodeGrayPng(edges, width, height),
    };
  } finally {
    scene.overrideMaterial = prevOverride;
    scene.background = prevBackground;
    gl.setRenderTarget(prevTarget);
    gl.autoClear = prevAutoClear;
    if (hadAspect) {
      aspectCamera.aspect = previousAspect;
      aspectCamera.updateProjectionMatrix();
    }
    target.dispose();
  }
}

// ---------------------------------------------------------------------------
// Depth
// ---------------------------------------------------------------------------

/**
 * RGBA-packed NDC depth → linear view-space depth in [0, 1] (0 = at the near
 * plane, 1 = at/behind the far plane). Rows are flipped here too: GL reads
 * bottom-up, images are top-down.
 */
function unpackDepth(
  packed: Uint8Array,
  width: number,
  height: number,
  camera: Camera,
): Float32Array {
  const persp = camera as PerspectiveCamera;
  const near = typeof persp.near === 'number' ? persp.near : 0.1;
  const far = typeof persp.far === 'number' ? persp.far : 1000;
  const out = new Float32Array(width * height);

  // three.js packRGBAToDepth factors, inverted.
  const S = 1 / 255;
  for (let y = 0; y < height; y += 1) {
    const srcRow = (height - 1 - y) * width;
    const dstRow = y * width;
    for (let x = 0; x < width; x += 1) {
      const i = (srcRow + x) * 4;
      const ndc =
        (packed[i] ?? 0) * S +
        (packed[i + 1] ?? 0) * (S / 255) +
        (packed[i + 2] ?? 0) * (S / 65025) +
        (packed[i + 3] ?? 0) * (S / 16581375);
      // Perspective: NDC depth → view Z, then normalise near..far → 0..1.
      const clamped = Math.min(Math.max(ndc, 0), 1);
      const viewZ = (near * far) / (far - clamped * (far - near));
      out[dstRow + x] = Math.min(Math.max((viewZ - near) / (far - near), 0), 1);
    }
  }
  return out;
}

/** ControlNet-depth convention: near = bright, far = dark. */
function depthToBrightness(depth: Float32Array): Uint8ClampedArray {
  const out = new Uint8ClampedArray(depth.length);
  for (let i = 0; i < depth.length; i += 1) {
    out[i] = Math.round((1 - (depth[i] ?? 1)) * 255);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Edges — Sobel, by hand (§9: "write the shader/JS yourself, no new dep")
// ---------------------------------------------------------------------------

/**
 * Sobel gradient magnitude over the linear depth field, drawn as dark lines on
 * white — the polarity `services/render/mock.py` multiplies into the render.
 */
export function sobelEdges(depth: Float32Array, width: number, height: number): Uint8ClampedArray {
  const out = new Uint8ClampedArray(width * height).fill(255);
  const at = (x: number, y: number): number => {
    const cx = Math.min(Math.max(x, 0), width - 1);
    const cy = Math.min(Math.max(y, 0), height - 1);
    return depth[cy * width + cx] ?? 1;
  };
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      // Kernels:  gx = [-1 0 1; -2 0 2; -1 0 1]   gy = gxᵀ
      const gx =
        -at(x - 1, y - 1) +
        at(x + 1, y - 1) -
        2 * at(x - 1, y) +
        2 * at(x + 1, y) -
        at(x - 1, y + 1) +
        at(x + 1, y + 1);
      const gy =
        -at(x - 1, y - 1) -
        2 * at(x, y - 1) -
        at(x + 1, y - 1) +
        at(x - 1, y + 1) +
        2 * at(x, y + 1) +
        at(x + 1, y + 1);
      const magnitude = Math.min(Math.hypot(gx, gy) * EDGE_GAIN, 1);
      out[y * width + x] = Math.round((1 - magnitude) * 255);
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// PNG encoding (DOM canvas — no GL context, so not "a second Canvas")
// ---------------------------------------------------------------------------

function encodeViaCanvas(pixels: Uint8ClampedArray, width: number, height: number): string {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d');
  if (context === null) throw new Error('capture: 2D canvas context unavailable');
  context.putImageData(new ImageData(pixels, width, height), 0, 0);
  const dataUrl = canvas.toDataURL('image/png');
  return dataUrl.slice(dataUrl.indexOf(',') + 1); // wire shape: no data: prefix
}

/**
 * RGBA readback → opaque PNG. Rows flip (GL is bottom-up), linear → sRGB (the
 * render target is written linear; the screen image the user compares against
 * is sRGB), and alpha flattens onto paper white — Pillow's `convert("RGB")`
 * would otherwise land transparent sky on black.
 */
function encodeColourPng(colour: Uint8Array, width: number, height: number): string {
  const out = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y += 1) {
    const srcRow = (height - 1 - y) * width;
    const dstRow = y * width;
    for (let x = 0; x < width; x += 1) {
      const s = (srcRow + x) * 4;
      const d = (dstRow + x) * 4;
      const a = (colour[s + 3] ?? 0) / 255;
      out[d] = blend(linearToSrgb((colour[s] ?? 0) / 255), BACKGROUND.r, a);
      out[d + 1] = blend(linearToSrgb((colour[s + 1] ?? 0) / 255), BACKGROUND.g, a);
      out[d + 2] = blend(linearToSrgb((colour[s + 2] ?? 0) / 255), BACKGROUND.b, a);
      out[d + 3] = 255;
    }
  }
  return encodeViaCanvas(out, width, height);
}

function encodeGrayPng(gray: Uint8ClampedArray, width: number, height: number): string {
  const out = new Uint8ClampedArray(width * height * 4);
  for (let i = 0; i < gray.length; i += 1) {
    const v = gray[i] ?? 0;
    out[i * 4] = v;
    out[i * 4 + 1] = v;
    out[i * 4 + 2] = v;
    out[i * 4 + 3] = 255;
  }
  return encodeViaCanvas(out, width, height);
}

function linearToSrgb(v: number): number {
  const c = Math.min(Math.max(v, 0), 1);
  return (c <= 0.0031308 ? c * 12.92 : 1.055 * Math.pow(c, 1 / 2.4) - 0.055) * 255;
}

function blend(fg: number, bg: number, alpha: number): number {
  return Math.round(fg * alpha + bg * (1 - alpha));
}
