/**
 * layerSpecs.ts — the TypeScript mirror of the nine §7 drawing layers.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THIS FILE IS A MIRROR, NOT A SOURCE
 * ════════════════════════════════════════════════════════════════════════════
 * `services/drawings/layers.py` is the source. Its own header says why: the
 * nine names are "a hard contract with the outside world" — a municipal
 * reviewer opens the DXF in AutoCAD or LibreCAD and expects AIA-style layers,
 * and the Python `LAYERS` tuple is the single ordered list the DXF writer, the
 * SVG renderer and the golden sheet tests all read.
 *
 * The web app cannot import Python, so this table exists. A second table is a
 * second source of truth and drifts — which is why `layerSpecs.test.ts` READS
 * `services/drawings/layers.py` at test time, parses its `LAYERS` tuple, and
 * fails if a single name, colour, linetype, lineweight or description here
 * disagrees with it. Change Python, and this file goes red until it follows.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY ACI CODES AND NOT HEX
 * ════════════════════════════════════════════════════════════════════════════
 * Colours are AutoCAD Color Index values because that is what the DXF carries.
 * Storing hex here would mean inventing a mapping that the DXF does not have,
 * and then arguing with it. {@link aciSwatchHex} does the presentation-side
 * conversion in exactly one place, and it is explicit about ACI 7 — which is
 * not a colour at all but the instruction "black on white paper, white on a
 * dark screen". That one returns `null`, and the panel paints it with the
 * theme's ink token so it behaves the same way on screen as it does on paper.
 */

/** The nine names, in the order `LAYERS` declares them. */
export const DRAWING_LAYER_NAMES = [
  'A-WALL',
  'A-WALL-PART',
  'A-DOOR',
  'A-WIND',
  'A-STAIR',
  'A-DIM',
  'A-TEXT',
  'A-AREA',
  'A-TITL',
] as const;

export type DrawingLayerName = (typeof DRAWING_LAYER_NAMES)[number];

/**
 * One layer, mirroring Python's `LayerSpec` field for field.
 *
 * `onCanvas` is the one field Python does not have, and it is deliberate: the
 * drawings service renders sheets, this app renders an editable plan, and the
 * two do not draw the same set. It is declared here rather than derived so
 * that `mapping.test.ts` can hold every `onCanvas: true` layer to the promise
 * that hiding it CHANGES WHAT THE PLAN DRAWS — a layer that claims canvas
 * presence and does nothing is exactly the "gate that silently never fires"
 * this repository has shipped before.
 */
export interface DrawingLayerSpec {
  readonly name: DrawingLayerName;
  /** AutoCAD Color Index. 1 red · 2 yellow · 3 green · 4 cyan · 5 blue · 6 magenta · 7 black/white · 8 grey. */
  readonly aci: number;
  readonly linetype: string;
  /** Lineweight in 1/100 mm, ezdxf's unit. -3 means "by default". */
  readonly lineweightHundredthsMm: number;
  readonly description: string;
  /** Python's `plottable`: a layer with no built geometry is not printed. */
  readonly plottable: boolean;
  /**
   * True when the 2D plan editor draws this layer's content. False for layers
   * that only exist on a finished sheet — the title block is drawn by the
   * sheet frame, never by the canvas, so a canvas toggle for it would be a
   * control that does nothing.
   */
  readonly onCanvas: boolean;
  /** Short human label for the panel; the name itself is the CAD identity. */
  readonly label: string;
}

/**
 * The mirror. Order matters — it is the DXF layer-creation order, the order of
 * the layer table in the docs, and the order the panel lists.
 */
export const DRAWING_LAYER_SPECS: readonly DrawingLayerSpec[] = [
  {
    name: 'A-WALL',
    aci: 7,
    linetype: 'CONTINUOUS',
    lineweightHundredthsMm: 50,
    description: 'Full-height wall outlines',
    plottable: true,
    onCanvas: true,
    label: 'Walls',
  },
  {
    name: 'A-WALL-PART',
    aci: 8,
    linetype: 'CONTINUOUS',
    lineweightHundredthsMm: 25,
    description: 'Partial-height walls, parapets, sills',
    plottable: true,
    onCanvas: true,
    label: 'Partial walls',
  },
  {
    name: 'A-DOOR',
    aci: 3,
    linetype: 'CONTINUOUS',
    lineweightHundredthsMm: 25,
    description: 'Door leaves and swing arcs',
    plottable: true,
    onCanvas: true,
    label: 'Doors',
  },
  {
    name: 'A-WIND',
    aci: 4,
    linetype: 'CONTINUOUS',
    lineweightHundredthsMm: 25,
    description: 'Window frames and glazing lines',
    plottable: true,
    onCanvas: true,
    label: 'Windows',
  },
  {
    name: 'A-STAIR',
    aci: 6,
    linetype: 'CONTINUOUS',
    lineweightHundredthsMm: 25,
    description: 'Stair treads, nosing and up arrow',
    plottable: true,
    onCanvas: true,
    label: 'Stairs',
  },
  {
    name: 'A-DIM',
    aci: 1,
    linetype: 'CONTINUOUS',
    lineweightHundredthsMm: 13,
    description: 'Dimension chains, witness and leader lines',
    plottable: true,
    onCanvas: true,
    label: 'Dimensions',
  },
  {
    name: 'A-TEXT',
    aci: 5,
    linetype: 'CONTINUOUS',
    lineweightHundredthsMm: 18,
    description: 'Room names, notes and callouts',
    plottable: true,
    onCanvas: true,
    label: 'Text',
  },
  {
    name: 'A-AREA',
    aci: 2,
    linetype: 'DASHED',
    lineweightHundredthsMm: 13,
    description: 'Room area boundaries and hatch outlines',
    plottable: true,
    onCanvas: true,
    label: 'Room areas',
  },
  {
    name: 'A-TITL',
    aci: 7,
    linetype: 'CONTINUOUS',
    lineweightHundredthsMm: 35,
    description: 'Sheet frame and title block',
    plottable: true,
    // The plan editor has no sheet frame — that is drawn by the drawings
    // service (`services/drawings/render/frame.py`). Listing it keeps the
    // panel a faithful picture of the DXF the architect will export, and the
    // panel says so rather than offering a toggle that changes nothing.
    onCanvas: false,
    label: 'Title block',
  },
];

const SPECS_BY_NAME: ReadonlyMap<DrawingLayerName, DrawingLayerSpec> = new Map(
  DRAWING_LAYER_SPECS.map((spec) => [spec.name, spec]),
);

/**
 * Look a layer up, failing loudly on a typo — the same posture as Python's
 * `layer_for`, and for the same reason: a stray layer name is a silent hole in
 * whatever was meant to be gated by it.
 */
export function layerSpec(name: DrawingLayerName): DrawingLayerSpec {
  const spec = SPECS_BY_NAME.get(name);
  if (spec === undefined) {
    throw new Error(
      `${String(name)} is not one of the nine §7 layers (${DRAWING_LAYER_NAMES.join(', ')}). ` +
        'Adding a layer changes what a municipal reviewer sees — do it in ' +
        'services/drawings/layers.py first, then mirror it here.',
    );
  }
  return spec;
}

/** True when `value` is one of the nine names. Used when reading storage. */
export function isDrawingLayerName(value: unknown): value is DrawingLayerName {
  return typeof value === 'string' && SPECS_BY_NAME.has(value as DrawingLayerName);
}

/** The layers the plan editor actually draws — the eight that are not A-TITL. */
export const CANVAS_DRAWING_LAYERS: readonly DrawingLayerName[] = DRAWING_LAYER_SPECS.filter(
  (spec) => spec.onCanvas,
).map((spec) => spec.name);

/**
 * ACI → CSS colour for a swatch, or `null` for ACI 7.
 *
 * ACI 7 is not a colour: it means "whatever contrasts with the paper". The
 * caller paints those with the theme's ink token so the swatch behaves the way
 * the plotted line does in both themes. Anything outside the eight codes the
 * layer table uses also returns `null` rather than a guess — an invented
 * colour on a compliance-adjacent surface is worse than an honest neutral.
 *
 * The seven values below are AutoCAD's own RGB for those indices, not a
 * prettier interpretation of them. The architect is choosing layers they will
 * later see in AutoCAD; a swatch that disagrees with the CAD palette teaches
 * the wrong thing. Legibility is the swatch's job (it is a filled dot inside a
 * ring), not the colour's.
 */
export function aciSwatchHex(aci: number): string | null {
  switch (aci) {
    case 1:
      return '#ff0000';
    case 2:
      return '#ffff00';
    case 3:
      return '#00ff00';
    case 4:
      return '#00ffff';
    case 5:
      return '#0000ff';
    case 6:
      return '#ff00ff';
    case 8:
      return '#808080';
    default:
      return null;
  }
}
