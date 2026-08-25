/**
 * `pages/project/three` — the 3D view's page-side composition, mirroring
 * `pages/project/plan` (the same DECISIONS.md rationale: scene composition
 * with a page's address, owned by the integrator; nothing in here knows it is
 * in a page and it moves to `features/canvas/scene3d/` unchanged when someone
 * owns that).
 *
 * The editor page (`PlanPage.tsx` — one page, both tabs, both camera modes)
 * mounts `ThreeDLayers` inside the shared `<CanvasRoot>` when the view is 3D,
 * and the two control components in the DOM overlay.
 */

export { ThreeDLayers, elementStoreyFflMm, visibleGroupKeysFor } from './ThreeDLayers';
export type { ThreeDLayersProps } from './ThreeDLayers';
export { StoreyVisibilityBar, ThreeDStatusChip } from './ThreeDControls';
