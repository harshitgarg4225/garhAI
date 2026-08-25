/**
 * ThreeDPage — the 3D + facade tab (F5, playbook Phase 5). No longer a
 * placeholder: this IS the real 3D view.
 *
 * It is an alias on purpose. §12 requires one scene graph and one hit-testing
 * system across 2D and 3D, and the Tab binding must swap the camera rig and
 * the layer set IN PLACE — same scene, same selection, same picker. A second
 * page component would remount `<CanvasRoot>` on every 2D↔3D switch: a new
 * PickRegistry, re-synthesised meshes, a fresh Manifold warm-up. So the
 * editor lives once, in `PlanPage.tsx`, hosts both camera modes, and reads
 * the `:tab` URL segment to decide which one is live; `routes.tsx` mounts the
 * SAME lazy component for both tabs so React reconciles the switch without
 * unmounting anything.
 *
 * What the 3D mode of that one page mounts (all Phase-5 modules, inside the
 * existing canvas):
 *
 *   pages/project/three/ThreeDLayers   the extruded building (ThreeDScene),
 *                                      the facade kit meshes (FacadeLayer),
 *                                      the sun light, the selection bridge
 *   FacadeKitPanel · SunPanel          docked overlay panels (apply/edit kits,
 *                                      scrub the sun through the day)
 *   NavModeHud + useNav3d              orbit · walk · fit
 *   StoreyVisibilityBar                see one storey / the whole building
 *   ThreeDStatusChip                   §14 rebuild ms + boolean-engine honesty
 *   MaterialsPanel                     in the inspector rail (ProjectShell)
 *
 * This module keeps the `ThreeDPage` name alive for `pages/index.ts`
 * consumers and for anything that mounts the tab directly.
 */

export { PlanPage as ThreeDPage, PlanPage as default } from './PlanPage';
