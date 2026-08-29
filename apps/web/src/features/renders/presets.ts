/**
 * presets.ts — the render preset catalogue and the client pack, mirrored.
 *
 * **Byte-identical mirror of `services/render/types.py` (PRESETS) and
 * `services/render/pack.py` (CLIENT_PACK_SHOTS).** The worker is the authority
 * — it validates every request — but the UI needs the same catalogue to draw
 * swatches, to gate the mode toggle (interiors are Explore-only at MVP, spec
 * F6) and to compose the one-click pack. Change the Python first, then this
 * file in the same commit; `renders.py` in the API carries the third copy.
 *
 * §9 sizes: renders default to 1536×1024 (`RenderIn` in
 * `garh_api/schemas/jobs.py`), and the capture set is taken at exactly the
 * requested output size so the ControlNet maps line up pixel-for-pixel.
 */

export type RenderMode = 'precise' | 'explore';

export interface RenderPresetInfo {
  readonly id: string;
  readonly label: string;
  readonly scene: 'exterior' | 'interior';
  readonly modes: readonly RenderMode[];
  /** The mock provider's grading tints — doubles as the UI swatch. */
  readonly tint: string;
  readonly tintSecondary: string;
  /** Which room type an interior preset aims at; null for exteriors. */
  readonly roomType: 'living' | 'kitchen' | 'bedroom' | null;
}

export const RENDER_PRESETS: readonly RenderPresetInfo[] = [
  {
    id: 'exterior-street-day',
    label: 'Street view, daylight',
    scene: 'exterior',
    modes: ['precise', 'explore'],
    tint: '#c4d6eb',
    tintSecondary: '#f6f0e2',
    roomType: null,
  },
  {
    id: 'exterior-34-dusk',
    label: 'Three-quarter view, dusk',
    scene: 'exterior',
    modes: ['precise', 'explore'],
    tint: '#485480',
    tintSecondary: '#ee9c6a',
    roomType: null,
  },
  {
    id: 'exterior-34-day',
    label: 'Three-quarter view, daylight',
    scene: 'exterior',
    modes: ['precise', 'explore'],
    tint: '#bad0e8',
    tintSecondary: '#faf6e8',
    roomType: null,
  },
  {
    id: 'exterior-night',
    label: 'Night, warm interior glow',
    scene: 'exterior',
    modes: ['precise', 'explore'],
    tint: '#18203a',
    tintSecondary: '#e2ac60',
    roomType: null,
  },
  {
    id: 'interior-living',
    label: 'Living room',
    scene: 'interior',
    modes: ['explore'],
    tint: '#e2d6c6',
    tintSecondary: '#faf6ee',
    roomType: 'living',
  },
  {
    id: 'interior-bedroom',
    label: 'Bedroom',
    scene: 'interior',
    modes: ['explore'],
    tint: '#d8cec8',
    tintSecondary: '#f6f2ec',
    roomType: 'bedroom',
  },
  {
    id: 'interior-kitchen',
    label: 'Kitchen',
    scene: 'interior',
    modes: ['explore'],
    tint: '#d6dad6',
    tintSecondary: '#f8f8f4',
    roomType: 'kitchen',
  },

  // The orientation-aware elevation presets. Precise only: an elevation render is
  // a drawing-check against a named face, and Explore may reinterpret the geometry.
  // Their LIGHT is deliberately NOT in this table — it is computed per project from
  // `plot.north` in services/render/orientation.py, because the same 'north
  // elevation' points a different way on every plot.
  {
    id: 'elevation-north-morning',
    label: 'North elevation, morning light',
    scene: 'exterior',
    modes: ['precise'],
    tint: '#c6d8e8',
    tintSecondary: '#faf4e2',
    roomType: null,
  },
  {
    id: 'elevation-north-midday',
    label: 'North elevation, midday',
    scene: 'exterior',
    modes: ['precise'],
    tint: '#bcd2ec',
    tintSecondary: '#fcfaf0',
    roomType: null,
  },
  {
    id: 'elevation-north-afternoon',
    label: 'North elevation, afternoon light',
    scene: 'exterior',
    modes: ['precise'],
    tint: '#e8c8a4',
    tintSecondary: '#fae8ca',
    roomType: null,
  },
  {
    id: 'elevation-north-evening',
    label: 'North elevation, evening light',
    scene: 'exterior',
    modes: ['precise'],
    tint: '#606894',
    tintSecondary: '#eea670',
    roomType: null,
  },
  {
    id: 'elevation-east-morning',
    label: 'East elevation, morning light',
    scene: 'exterior',
    modes: ['precise'],
    tint: '#c6d8e8',
    tintSecondary: '#faf4e2',
    roomType: null,
  },
  {
    id: 'elevation-east-midday',
    label: 'East elevation, midday',
    scene: 'exterior',
    modes: ['precise'],
    tint: '#bcd2ec',
    tintSecondary: '#fcfaf0',
    roomType: null,
  },
  {
    id: 'elevation-east-afternoon',
    label: 'East elevation, afternoon light',
    scene: 'exterior',
    modes: ['precise'],
    tint: '#e8c8a4',
    tintSecondary: '#fae8ca',
    roomType: null,
  },
  {
    id: 'elevation-east-evening',
    label: 'East elevation, evening light',
    scene: 'exterior',
    modes: ['precise'],
    tint: '#606894',
    tintSecondary: '#eea670',
    roomType: null,
  },
  {
    id: 'elevation-south-morning',
    label: 'South elevation, morning light',
    scene: 'exterior',
    modes: ['precise'],
    tint: '#c6d8e8',
    tintSecondary: '#faf4e2',
    roomType: null,
  },
  {
    id: 'elevation-south-midday',
    label: 'South elevation, midday',
    scene: 'exterior',
    modes: ['precise'],
    tint: '#bcd2ec',
    tintSecondary: '#fcfaf0',
    roomType: null,
  },
  {
    id: 'elevation-south-afternoon',
    label: 'South elevation, afternoon light',
    scene: 'exterior',
    modes: ['precise'],
    tint: '#e8c8a4',
    tintSecondary: '#fae8ca',
    roomType: null,
  },
  {
    id: 'elevation-south-evening',
    label: 'South elevation, evening light',
    scene: 'exterior',
    modes: ['precise'],
    tint: '#606894',
    tintSecondary: '#eea670',
    roomType: null,
  },
  {
    id: 'elevation-west-morning',
    label: 'West elevation, morning light',
    scene: 'exterior',
    modes: ['precise'],
    tint: '#c6d8e8',
    tintSecondary: '#faf4e2',
    roomType: null,
  },
  {
    id: 'elevation-west-midday',
    label: 'West elevation, midday',
    scene: 'exterior',
    modes: ['precise'],
    tint: '#bcd2ec',
    tintSecondary: '#fcfaf0',
    roomType: null,
  },
  {
    id: 'elevation-west-afternoon',
    label: 'West elevation, afternoon light',
    scene: 'exterior',
    modes: ['precise'],
    tint: '#e8c8a4',
    tintSecondary: '#fae8ca',
    roomType: null,
  },
  {
    id: 'elevation-west-evening',
    label: 'West elevation, evening light',
    scene: 'exterior',
    modes: ['precise'],
    tint: '#606894',
    tintSecondary: '#eea670',
    roomType: null,
  },
] as const;

export const PRESETS_BY_ID: ReadonlyMap<string, RenderPresetInfo> = new Map(
  RENDER_PRESETS.map((p) => [p.id, p]),
);

export const DEFAULT_PRESET_ID = 'exterior-street-day';

/** §9 default output size — matches the API's `RenderIn` defaults. */
export const DEFAULT_RENDER_SIZE = { width: 1536, height: 1024 } as const;

export interface PackShot {
  readonly slug: string;
  readonly preset: string;
  readonly mode: RenderMode;
}

/** The §9 client pack: 6 exteriors + living + kitchen, in zip order. */
export const CLIENT_PACK_SHOTS: readonly PackShot[] = [
  { slug: 'exterior-street-day', preset: 'exterior-street-day', mode: 'precise' },
  { slug: 'exterior-34-day', preset: 'exterior-34-day', mode: 'precise' },
  { slug: 'exterior-34-dusk', preset: 'exterior-34-dusk', mode: 'precise' },
  { slug: 'exterior-night', preset: 'exterior-night', mode: 'precise' },
  { slug: 'exterior-street-day-explore', preset: 'exterior-street-day', mode: 'explore' },
  { slug: 'exterior-34-dusk-explore', preset: 'exterior-34-dusk', mode: 'explore' },
  { slug: 'interior-living', preset: 'interior-living', mode: 'explore' },
  { slug: 'interior-kitchen', preset: 'interior-kitchen', mode: 'explore' },
] as const;

/**
 * §9 (Forma's contract), in the plain words the UI shows. No jargon — §15's
 * tone rule. "Geometry-locked" vs "moodboard" is the entire distinction.
 */
export const MODE_COPY: Readonly<Record<RenderMode, { title: string; body: string }>> = {
  precise: {
    title: 'Precise — your building, exactly',
    body: 'The image follows your model line for line. Walls, windows and roofs stay where you put them — use this for client sign-off.',
  },
  explore: {
    title: 'Explore — the mood, loosely',
    body: 'The image treats your model as inspiration, not instruction. Shapes may drift — use this for materials, lighting and feel, like a moodboard.',
  },
};

/** A fresh random seed the user can see and keep. */
export function randomSeed(): number {
  return Math.floor(Math.random() * 1_000_000);
}
