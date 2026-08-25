/**
 * features/renders — Phase 7 (§9): viewport capture from the ONE live canvas,
 * render jobs with honest SSE progress, version-pinned history with the stale
 * banner, and the one-click client pack.
 *
 * Integration surface (what the pages mount):
 *   `RenderCaptureBridge`  inside the `<Canvas>` — publishes the live renderer
 *   `RenderLauncher`       the 3D view's overlay panel — captures + submits
 *   `RendersTab`           the Renders tab body — history, packs, downloads
 */

export { RendersTab, default as RendersTabDefault } from './RendersTab';
export { RenderLauncher } from './RenderLauncher';
export { RenderCaptureBridge, captureSource, subscribeCaptureSource } from './captureBridge';
export type { CaptureSource } from './captureBridge';
export { captureSet, sobelEdges } from './capture';
export type { CaptureSet, CaptureSize } from './capture';
export { presetCamera, buildingBboxMm, PresetCameraError } from './cameras';
export type { PresetView } from './cameras';
export {
  CLIENT_PACK_SHOTS,
  DEFAULT_PRESET_ID,
  DEFAULT_RENDER_SIZE,
  MODE_COPY,
  PRESETS_BY_ID,
  RENDER_PRESETS,
  randomSeed,
} from './presets';
export type { RenderMode, RenderPresetInfo, PackShot } from './presets';
export { useRendersUiStore } from './store';
export type { PendingRenderRequest } from './store';
export { useRenderHistory } from './useRenderHistory';
export { renderShareMessage, waShareUrl } from './whatsapp';
export {
  archiveRenderPack,
  getRenderPack,
  listRenderHistory,
  startClientPack,
  startRender,
} from './api';
export type { RenderJob, RenderPack, PackArchive } from './api';
