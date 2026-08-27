/**
 * lines.ts — batched line geometry for the overlay layers. PURE (no three).
 *
 * Every dimension string in a plan is drawn as ONE `THREE.LineSegments` with
 * one material and one draw call. The alternative — a `<Line>` per witness
 * line, per tick, per baseline — is several hundred draw calls and several
 * hundred materials on a G+2, which is the §14 budget spent before any pixels
 * are shaded.
 *
 * The buffer is written IN PLACE by the layer on every camera commit (dimension
 * baselines are a screen-space offset from the building, so they move with
 * zoom). That is why these functions take a target array and return a count
 * instead of allocating: an allocation per frame is a garbage-collection pause
 * per zoom gesture, and a 16 ms budget does not absorb one.
 *
 * Coordinates go in as plan millimetres and come out as WORLD units (metres,
 * Y-up, north = −Z) — the same transform `core/coords.ts` documents, applied
 * inline here because it is the innermost loop of the overlay renderer.
 */

/** World units per millimetre. Mirrors `core/constants.ts` — 1 unit = 1 metre. */
const WORLD_PER_MM = 0.001;

/** Floats per vertex (x, y, z) and vertices per line segment. */
export const FLOATS_PER_VERTEX = 3;
export const VERTICES_PER_SEGMENT = 2;
export const FLOATS_PER_SEGMENT = FLOATS_PER_VERTEX * VERTICES_PER_SEGMENT;

/**
 * A growable float buffer for line-segment positions.
 *
 * Grows by 1.5× and NEVER shrinks: a plan that briefly showed 400 dimension
 * segments will show them again, and re-allocating on the way back down trades
 * a few kilobytes of memory for a GC pause in the middle of an interaction.
 */
export class LineBuffer {
  private data: Float32Array;

  /** Floats written by the last `begin`/`push` pass. */
  private used = 0;

  constructor(initialSegments = 64) {
    this.data = new Float32Array(Math.max(1, initialSegments) * FLOATS_PER_SEGMENT);
  }

  /** The backing array. Identity changes only when the buffer grows. */
  get array(): Float32Array {
    return this.data;
  }

  /** Vertices to draw: `geometry.setDrawRange(0, this.vertexCount)`. */
  get vertexCount(): number {
    return this.used / FLOATS_PER_VERTEX;
  }

  /** Capacity in segments. */
  get capacity(): number {
    return this.data.length / FLOATS_PER_SEGMENT;
  }

  /** Start a write pass. Does not clear — unwritten tail is never drawn. */
  begin(): void {
    this.used = 0;
  }

  /**
   * Make room for `segments` more segments.
   * Returns true when the backing array was replaced, which is the caller's cue
   * to rebuild the `BufferAttribute` (the only case where that is necessary).
   */
  reserve(segments: number): boolean {
    const needed = this.used + segments * FLOATS_PER_SEGMENT;
    if (needed <= this.data.length) return false;
    let next = this.data.length;
    while (next < needed) next = Math.ceil(next * 1.5);
    const grown = new Float32Array(next);
    grown.set(this.data.subarray(0, this.used));
    this.data = grown;
    return true;
  }

  /**
   * Append one plan-space line segment at `elevationMm`.
   *
   * Silently drops the segment when there is no capacity: `reserve` is the
   * caller's job, and throwing inside a render loop turns a cosmetic overflow
   * into a blank canvas.
   */
  push(x1Mm: number, y1Mm: number, x2Mm: number, y2Mm: number, elevationMm = 0): void {
    if (this.used + FLOATS_PER_SEGMENT > this.data.length) return;
    const y = elevationMm * WORLD_PER_MM;
    const d = this.data;
    let i = this.used;
    d[i] = x1Mm * WORLD_PER_MM;
    d[i + 1] = y;
    d[i + 2] = -y1Mm * WORLD_PER_MM;
    i += 3;
    d[i] = x2Mm * WORLD_PER_MM;
    d[i + 1] = y;
    d[i + 2] = -y2Mm * WORLD_PER_MM;
    this.used += FLOATS_PER_SEGMENT;
  }

  /** Append a polyline as `n − 1` segments. */
  pushPolyline(pointsMm: readonly { x: number; y: number }[], elevationMm = 0): void {
    for (let i = 0; i + 1 < pointsMm.length; i++) {
      const a = pointsMm[i];
      const b = pointsMm[i + 1];
      if (a === undefined || b === undefined) continue;
      this.push(a.x, a.y, b.x, b.y, elevationMm);
    }
  }
}

// ---------------------------------------------------------------------------
// Tick marks
// ---------------------------------------------------------------------------

/**
 * The architectural tick: a 45° slash through the dimension line, not an arrow.
 *
 * Arrowheads need a filled triangle (a second geometry and a second draw call)
 * and read as clutter at plan scale. The slash is what a hand-drafted sheet
 * uses and what the §7 DXF `A-DIM` layer will emit, so the canvas and the sheet
 * show the same mark.
 */
export function pushTick(
  buffer: LineBuffer,
  xMm: number,
  yMm: number,
  axis: 'x' | 'y',
  halfMm: number,
  elevationMm = 0,
): void {
  // The slash leans the same way relative to the string in both orientations,
  // which is what makes a stack of horizontal and vertical strings look drawn
  // by one hand rather than mirrored.
  if (axis === 'x') {
    buffer.push(xMm - halfMm, yMm - halfMm, xMm + halfMm, yMm + halfMm, elevationMm);
  } else {
    buffer.push(xMm - halfMm, yMm + halfMm, xMm + halfMm, yMm - halfMm, elevationMm);
  }
}
