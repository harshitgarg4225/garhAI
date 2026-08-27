/**
 * `features/canvas/overlays` — everything drawn ON TOP of the plan, plus the
 * two panels that read the same data.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHAT THIS MODULE OWNS
 * ────────────────────────────────────────────────────────────────────────────
 *   dimensions/  live dimension chains, click-to-edit → wall.move / opening.*
 *   tags/        room name + area from `rooms.ts`, non-overlapping, editable
 *   compliance/  the debounced chip strip, on-canvas markers, zoom-to-violation
 *   inspector/   selection properties, every field an op
 *   render/      batched line geometry, screen-constant scaling, materials
 *   format.ts    the mm ↔ human boundary for everything above
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHAT IT DOES NOT OWN, AND MUST NOT
 * ────────────────────────────────────────────────────────────────────────────
 *   · the scene, the camera, the picker — `features/canvas/core`
 *   · room detection — `packages/model/src/rooms.ts`. This renders it.
 *   · unit parsing and formatting — `packages/model/src/units.ts` via `lib/units`
 *   · document state — `stores/model` is the only writer (golden rule 1)
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WIRING IT UP (what the Plan page does)
 * ────────────────────────────────────────────────────────────────────────────
 * ```tsx
 * const chains  = useMemo(
 *   () => buildDimensionChains(wallsOfStorey(house, storeyId), house.openings).chains,
 *   [house, storeyId],
 * );
 * const tags    = useMemo(() => roomTags(house.rooms, storeyId, units), [house, storeyId, units]);
 * const editing = useDimensionEditing();
 * const indexRef = useRef<DimensionHandleIndex | null>(null);
 * const overlay = useComplianceOverlay({ issues, checking, house, activeStoreyId: storeyId });
 *
 * <CanvasRoot
 *   onClick={(e) => {
 *     const hit = e.hit();
 *     if (hit.kind === 'dimension' && hit.id !== null && indexRef.current !== null) {
 *       editing.open(indexRef.current, hit.id, e.pixel);
 *     }
 *   }}
 *   overlay={
 *     <>
 *       {editing.session === null ? null : (
 *         <DimensionEditor
 *           atPx={editing.session.atPx}
 *           valueMm={editing.session.valueMm}
 *           display={units}
 *           error={editing.error}
 *           onCommit={(mm) => editing.commit(house, mm)}
 *           onCancel={editing.cancel}
 *         />
 *       )}
 *       <ComplianceChipStrip … />
 *     </>
 *   }
 * >
 *   <DimensionLayer chains={chains} display={units} storeyId={storeyId}
 *                   onHandleIndex={(i) => { indexRef.current = i; }} />
 *   <RoomTagLayer tags={tags} storeyId={storeyId} highlightIds={selectedIds} />
 *   <ComplianceMarkerLayer markers={overlay.markers} />
 * </CanvasRoot>
 * ```
 */

// ── The display boundary ───────────────────────────────────────────────────
export {
  AREA_DECIMALS,
  DIMENSION_BARE_UNIT,
  areaEditSeed,
  areaHint,
  dimensionEditSeed,
  dimensionHint,
  dimensionText,
  dimensionTextMm,
  expandFractionGlyphs,
  parseAreaInput,
  parseDimensionInput,
  roomAreaText,
} from './format';
export type { AreaParseResult, ParseResult } from './format';

// ── Dimensions ─────────────────────────────────────────────────────────────
export {
  buildDimensionChains,
  buildRoomSpanChains,
  chainBaselineMm,
  chainPointMm,
  DIM_LEVEL,
  editableSegments,
  segmentMidMm,
} from './dimensions/chain';
export type {
  DimAxis,
  DimChain,
  DimChainKind,
  DimensionChainOptions,
  DimensionChainSet,
  DimensionEditTarget,
  DimPointF,
  DimSegment,
  DimSide,
  DimTick,
} from './dimensions/chain';

export { applyDimensionEdit, roomNameOp, roomTargetAreaOp } from './dimensions/edit';
export type { DimensionEditResult } from './dimensions/edit';

export {
  buildHandleIndex,
  DimensionLayer,
  DIM_LABEL_PX,
  DIM_MIN_SEGMENT_PX,
  DIM_OFFSET_PX,
  DIM_PICK_HEIGHT_PX,
  DIM_STEP_PX,
} from './dimensions/DimensionLayer';
export type {
  DimensionHandle,
  DimensionHandleIndex,
  DimensionLayerProps,
} from './dimensions/DimensionLayer';

export { DimensionEditor } from './dimensions/DimensionEditor';
export type { DimensionEditorProps } from './dimensions/DimensionEditor';

export { useDimensionEditing } from './dimensions/useDimensionEditing';
export type { DimensionEditing, DimensionEditSession } from './dimensions/useDimensionEditing';

// ── Room tags ──────────────────────────────────────────────────────────────
export {
  AVG_GLYPH_RATIO,
  DEFAULT_TAG_STYLE,
  estimateTextWidth,
  roomAnchorMm,
  roomTags,
  roomTypeLabel,
  tagFitsOnScreen,
  tagsToPlaceable,
} from './tags/tags';
export type { RoomTagOptions, RoomTagVM, TagStyle } from './tags/tags';

export { overflowedLabels, placeLabels, shouldReplace, ZOOM_REPLACE_RATIO } from './tags/placement';
export type {
  LabelPlacementKind,
  PlaceableLabel,
  PlacedLabel,
  PlacementOptions,
  PlacePointF,
} from './tags/placement';

export { AREA_HANDLE_SUFFIX, parseRoomTagHandle, RoomTagLayer } from './tags/RoomTagLayer';
export type { RoomTagLayerProps } from './tags/RoomTagLayer';

// ── Compliance ─────────────────────────────────────────────────────────────
export {
  complianceCounts,
  DEFAULT_VISIBLE_STATUSES,
  elementBboxMm,
  FOCUS_PADDING_RATIO,
  focusFitBbox,
  focusFor,
  mapComplianceChips,
  markersFor,
} from './compliance/mapping';
export type { ComplianceChipVM, ComplianceFocus, ComplianceMarker } from './compliance/mapping';

export {
  COMPLIANCE_DEBOUNCE_BUDGET_MS,
  COMPLIANCE_MAP_DEBOUNCE_MS,
  useComplianceOverlay,
} from './compliance/useComplianceOverlay';
export type { ComplianceOverlay, ComplianceOverlayInput } from './compliance/useComplianceOverlay';

export { ComplianceChipStrip } from './compliance/ComplianceChipStrip';
export type { ComplianceChipStripProps } from './compliance/ComplianceChipStrip';

export { ComplianceMarkerLayer, MARKER_RADIUS_PX } from './compliance/ComplianceMarkerLayer';
export type { ComplianceMarkerLayerProps } from './compliance/ComplianceMarkerLayer';

// ── Inspector ──────────────────────────────────────────────────────────────
export { inspectorSelection, WALL_THICKNESSES_MM } from './inspector/fields';
export type {
  EnumOption,
  InspectorAction,
  InspectorField,
  InspectorFieldKind,
  InspectorOptions,
  InspectorSelection,
} from './inspector/fields';

export { InspectorPanel } from './inspector/InspectorPanel';
export type { InspectorPanelProps } from './inspector/InspectorPanel';

// ── Render plumbing (exported for Phase 5, which shares these layers) ──────
export { LineBuffer, pushTick } from './render/lines';
export {
  disposeOverlayMaterials,
  getOverlayMaterials,
  LABEL_FONT_SIZE_LOCAL,
  LABEL_FONT_URL,
  refreshOverlayMaterials,
} from './render/overlayMaterials';
export type { OverlayMaterials } from './render/overlayMaterials';
export { useScreenScale, useViewportEffect, worldPerPx } from './render/screenScale';
export type { ScreenScaleHandle } from './render/screenScale';
