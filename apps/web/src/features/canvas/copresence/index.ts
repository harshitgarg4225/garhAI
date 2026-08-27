/**
 * `features/canvas/copresence` — the two things on the plan that are about
 * PEOPLE rather than about the building: where your collaborators' pointers
 * are, and which points on the drawing somebody has commented on.
 *
 * Both are DOM chrome over the canvas rather than WebGL layers, and both are
 * deliberately outside the pick registry. `CoPresenceLayer`'s docblock carries
 * the full argument; the short version is that a comment is not an element of
 * the model, a cursor must never be clickable at all, and the per-user colour
 * is a design-token class pair that must stay identical to the presence chips'.
 *
 * Mounted once, from `PlanScene`.
 */

export { CoPresenceLayer, storeyIndexOf } from './CoPresenceLayer';
export type { CoPresenceLayerProps } from './CoPresenceLayer';
export { CanvasDomOverlay } from './CanvasDomOverlay';
export type { CanvasDomOverlayProps } from './CanvasDomOverlay';
export { CoPresenceOverlayUi } from './CoPresenceOverlayUi';
export type { CoPresenceOverlayUiProps, ProjectMm } from './CoPresenceOverlayUi';
export { createTrailingThrottle } from './cursorThrottle';
export type { TrailingThrottle, TrailingThrottleOptions } from './cursorThrottle';
export { projectMmToOverlay } from './overlayProjection';
export type { OverlayPoint } from './overlayProjection';
export { useCursorBroadcast, CURSOR_POST_INTERVAL_MS } from './useCursorBroadcast';
export type { CursorBroadcastOptions } from './useCursorBroadcast';
