/**
 * `features/canvas/sun` — the Phase-5 sun widget and (see the note in
 * `nav/orbitOps.ts`) the 3D navigation layer.
 *
 * WHAT THE INTEGRATOR MOUNTS
 *   <SunLight />          inside <CanvasRoot> — the directional sun + fill,
 *                         soft shadows framed to the building. Lights only:
 *                         nothing here is pickable and nothing registers.
 *   <SunPanel />          DOM overlay: DD-MM-YYYY date, IST time scrubber,
 *                         season presets, compass readout.
 *   useNav3d(el, {core})  3D navigation — orbit / zoom-to-cursor / fit /
 *                         walk. Mount with `navigation: viewMode === '2d'`
 *                         on `useCanvasControls`; see useNav3d's header.
 *   <NavModeHud nav />    Orbit · Walk · Fit buttons + the honest walk hint.
 *
 * THE ONE RULE: scrubbing the sun is a light-only update. Sun state lives in
 * `useSunStore`, outside the ProjectDoc; no op exists for it, no mesh reads
 * it, and `scrubInvariance.test.ts` pins the document hash across a full-day
 * scrub.
 */

export { solarPosition, refractionDeg, istToUtcMs, utcMsToIst, daysFromCivil, IST_UTC_OFFSET_MINUTES } from './solar';
export type { CalendarDate, SolarPosition } from './solar';

export { CITY_CENTROIDS, DEFAULT_CITY, cityForPack } from './cities';
export type { CityCentroid } from './cities';

export { formatDdMmYyyy, parseDdMmYyyy, formatMinutes, isValidCalendarDate, daysInMonth, isLeapYear } from './dateText';

export { useSunStore, initialSunFields, seasonPresets } from './sunStore';
export type { SunState } from './sunStore';

export { computeSunFrame, sunDirectionModel, compassLabel } from './frame';
export type { SunFrame } from './frame';

export { buildingExtentOf } from './buildingBbox';
export type { BuildingExtent } from './buildingBbox';

export { SunLight } from './SunLight';
export { SunPanel } from './SunPanel';
export type { SunPanelProps } from './SunPanel';

// 3D navigation (see nav/orbitOps.ts header for why it lives here)
export {
  dollyOrbitAboutAnchor,
  enterWalkOrbit,
  orbitFromWalkPose,
  walkPoseOf,
  walkStep,
  walkTurn,
  WALK_EYE_HEIGHT_MM,
  WALK_LOOK_DISTANCE_MM,
  WALK_RUN_FACTOR,
  WALK_SPEED_MM_PER_S,
} from './nav/orbitOps';
export type { WalkPose } from './nav/orbitOps';
export { useNav3d } from './nav/useNav3d';
export type { Nav3dApi, Nav3dOptions, NavMode } from './nav/useNav3d';
export { NavModeHud } from './nav/NavModeHud';
export type { NavModeHudProps } from './nav/NavModeHud';
