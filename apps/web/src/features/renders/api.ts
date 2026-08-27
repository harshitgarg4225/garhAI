/**
 * api.ts — the renders feature's transport.
 *
 * PHASE 7 INTEGRATION: the endpoint calls that used to live here (each with its
 * own zod copy of the render row) now delegate to `lib/api.ts::api.renders.*`,
 * which is the catalogued §11 surface. One schema, one place — and one real bug
 * fixed by the move: the shared `jobSchema` mislabels a render row `kind:
 * 'solver'` (the server sends no discriminator), which sent every render's SSE
 * subscription to the solver endpoint. `lib/schemas.ts::renderJobSchema` stamps
 * the discriminator from the endpoint that returned the row; see its note.
 *
 * What stays here, because it is genuinely feature-level and not an endpoint:
 * moving captured PNGs into object storage (`uploadCaptureSet`,
 * `deliverCaptureSet`) and the base64→Blob step that needs a browser.
 *
 * Every exported name and signature is unchanged, so `RendersTab`,
 * `RenderLauncher` and `useRenderHistory` did not move.
 */

import { api, type RenderCaptureInputs, type RenderPackShot } from '../../lib/api';
import type {
  ExportJob,
  Job,
  RenderJob,
  RenderMode,
  RenderPack,
  RenderUploadSlot,
} from '../../lib/schemas';

export type { RenderJob, RenderMode, RenderPack };
/** The archive is an ordinary export job — see `api.renders.archivePack`. */
export type PackArchive = ExportJob;
export type UploadSlot = RenderUploadSlot;
export type CaptureInputs = RenderCaptureInputs;
export type PackShotUpload = RenderPackShot;

export interface RenderPage {
  readonly items: RenderJob[];
  readonly nextCursor: string | null;
  readonly hasMore: boolean;
}

/**
 * A render row IS a `Job` (the schema's transform stamps `kind`, `type` and
 * `queuePosition`), so handing one to `useJobsStore.track` needs no adapter.
 * The function is kept as the named seam the launcher reads through — and as
 * the compile-time proof that the two shapes still agree.
 */
export function toTrackableJob(job: RenderJob): Job {
  return job;
}

/**
 * Deliver one capture set the best way available: presigned upload (keeps the
 * job body tiny — mandatory for packs, whose 24 images would blow the API's
 * request cap) with an honest inline-base64 fallback when storage is not
 * reachable from the browser (dev without minio CORS, tests).
 */
export async function deliverCaptureSet(
  projectId: string,
  captured: { viewportPng: string; depthPng: string; edgesPng: string },
): Promise<CaptureInputs> {
  try {
    const slots = await requestUploadSlots(projectId, 3);
    const [viewportUrl, depthUrl, edgesUrl] = await uploadCaptureSet(slots, [
      captured.viewportPng,
      captured.depthPng,
      captured.edgesPng,
    ]);
    if (viewportUrl === undefined) throw new Error('upload produced no URL');
    return { viewportUrl, depthUrl, edgesUrl };
  } catch {
    return {
      viewportPng: captured.viewportPng,
      depthPng: captured.depthPng,
      edgesPng: captured.edgesPng,
    };
  }
}

// ---------------------------------------------------------------------------
// Capture uploads — keeps a pack under the API's request-body cap
// ---------------------------------------------------------------------------

/** `POST /projects/:id/renders/uploads` — presigned PUT/GET pairs. */
export function requestUploadSlots(projectId: string, count: number): Promise<UploadSlot[]> {
  return api.renders.uploadSlots(projectId, count);
}

function base64ToBlob(b64: string): Blob {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: 'image/png' });
}

/**
 * Move one capture set (viewport/depth/edges, base64) into object storage and
 * return URL-shaped inputs. Throws on the first failed PUT — the caller falls
 * back to the inline path, which the API accepts for bodies under its cap.
 *
 * The PUTs go straight to storage with `fetch`, deliberately not through
 * `HttpClient`: the presigned URL carries its own authorisation in the query
 * string, and attaching this firm's bearer token to a third-party object store
 * would leak it (§13).
 */
export async function uploadCaptureSet(
  slots: readonly UploadSlot[],
  images: readonly string[],
): Promise<string[]> {
  if (slots.length < images.length) throw new Error('not enough upload slots');
  const urls: string[] = [];
  for (let i = 0; i < images.length; i += 1) {
    const slot = slots[i];
    const image = images[i];
    if (slot === undefined || image === undefined) throw new Error('upload slot mismatch');
    const response = await fetch(slot.putUrl, {
      method: 'PUT',
      body: base64ToBlob(image),
      headers: { 'content-type': 'image/png' },
    });
    if (!response.ok) throw new Error(`capture upload failed (HTTP ${response.status})`);
    urls.push(slot.getUrl);
  }
  return urls;
}

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------

export interface StartRenderInput {
  readonly projectId: string;
  readonly designVersionId?: string | null;
  readonly mode: RenderMode;
  readonly preset: string;
  readonly seed: number;
  readonly width: number;
  readonly height: number;
  readonly view: Record<string, unknown>;
  readonly inputs: CaptureInputs;
  readonly signal?: AbortSignal | undefined;
}

/** `POST /projects/:id/renders` with the full §9 body. */
export function startRender(input: StartRenderInput): Promise<RenderJob> {
  return api.renders.start({
    projectId: input.projectId,
    mode: input.mode,
    preset: input.preset,
    seed: input.seed,
    width: input.width,
    height: input.height,
    view: input.view,
    inputs: input.inputs,
    ...(input.designVersionId == null ? {} : { designVersionId: input.designVersionId }),
    ...(input.signal === undefined ? {} : { signal: input.signal }),
  });
}

/** `GET /projects/:id/render-history` — image links re-signed per request. */
export function listRenderHistory(
  projectId: string,
  options: { cursor?: string | null; limit?: number; signal?: AbortSignal } = {},
): Promise<RenderPage> {
  return api.renders.history(projectId, {
    cursor: options.cursor ?? null,
    ...(options.limit === undefined ? {} : { limit: options.limit }),
    ...(options.signal === undefined ? {} : { signal: options.signal }),
  });
}

export interface StartPackInput {
  readonly projectId: string;
  readonly designVersionId?: string | null;
  readonly seed: number;
  readonly width: number;
  readonly height: number;
  readonly shots: readonly PackShotUpload[];
  readonly signal?: AbortSignal | undefined;
}

/** `POST /projects/:id/renders/client-pack` — one job group (§9). */
export function startClientPack(input: StartPackInput): Promise<RenderPack> {
  return api.renders.clientPack({
    projectId: input.projectId,
    seed: input.seed,
    width: input.width,
    height: input.height,
    shots: input.shots,
    ...(input.designVersionId == null ? {} : { designVersionId: input.designVersionId }),
    ...(input.signal === undefined ? {} : { signal: input.signal }),
  });
}

export function getRenderPack(
  projectId: string,
  packId: string,
  options: { signal?: AbortSignal } = {},
): Promise<RenderPack> {
  return api.renders.pack(projectId, packId, {
    ...(options.signal === undefined ? {} : { signal: options.signal }),
  });
}

/** Zip the finished pack; returns the signed download (existing export path). */
export function archiveRenderPack(projectId: string, packId: string): Promise<PackArchive> {
  return api.renders.archivePack(projectId, packId);
}
