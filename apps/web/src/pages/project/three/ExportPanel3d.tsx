/**
 * ExportPanel3d — take the 3D model OUT of the building (J06, §F9 "glTF").
 *
 * Two paths, each honest about what it produces:
 *
 *  · **Download this view** — the live scene, through three's `GLTFExporter`
 *    (`features/canvas/three/gltfExport.ts`): the building as it is drawn
 *    right now — cut walls if the engine cut them, the facade kit, the storey
 *    filter honoured. Runs entirely in the browser; the file lands in the
 *    downloads folder as `<project>.glb`. Needs the canvas mounted, which on
 *    this tab it always is (`captureSource()` is the same handle the renders
 *    launcher photographs through — one scene, §12).
 *
 *  · **Export via server** — `POST /projects/:id/export kind=gltf`, drawn by
 *    the drawings worker FROM THE MODEL (metres, Y-up, one mesh per element
 *    class, openings by span-splitting) and handed back as a signed download
 *    on the job card below. Audited and metered like every export, and
 *    independent of what this browser can render.
 *
 * The job list below shows only this project's glTF exports, through the same
 * `JobCard` the Sheets tab uses — its Download link IS the signed URL.
 */

import { useCallback, useMemo, useState, useSyncExternalStore } from 'react';

import { Button, Icon, cn } from '@garh/ui';

import { JobList } from '../../../components';
import {
  countExportableMeshes,
  exportGlb,
  exportRootsOf,
  glbFileName,
} from '../../../features/canvas/three';
import { captureSource, subscribeCaptureSource } from '../../../features/renders';
import { AppError } from '../../../lib/errors';
import { useJobsStore } from '../../../stores/jobs';
import { useUiStore } from '../../../stores/ui';
import { toJobVM } from '../../_contracts';
import { useProjectOutlet } from '../../ProjectShell';

/** Hand the browser a file to save. A real anchor click, so it survives popup blockers. */
function saveBlob(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = fileName;
  anchor.rel = 'noopener';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Long enough for the download manager to open the blob; then free it.
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

export function ExportPanel3d({ className }: { className?: string | undefined }): JSX.Element {
  const { project } = useProjectOutlet();
  const source = useSyncExternalStore(subscribeCaptureSource, captureSource, () => null);
  const jobs = useJobsStore((s) => s.byProject[project.id]);
  const cancel = useJobsStore((s) => s.cancel);
  const retry = useJobsStore((s) => s.retry);
  const dismiss = useJobsStore((s) => s.dismiss);

  const [busy, setBusy] = useState<'client' | 'server' | null>(null);

  const glbJobs = useMemo(
    () =>
      (jobs ?? []).filter((job) => job.kind === 'export' && job.exportKind === 'gltf').map(toJobVM),
    [jobs],
  );

  const toast = useCallback(
    (input: Parameters<ReturnType<typeof useUiStore.getState>['pushToast']>[0]) => {
      useUiStore.getState().pushToast(input);
    },
    [],
  );

  const downloadThisView = useCallback(async () => {
    if (source === null) {
      toast({
        tone: 'warning',
        title: 'Nothing to export yet',
        description: 'Open the 3D view first — the download is the scene you are looking at.',
      });
      return;
    }
    setBusy('client');
    try {
      const roots = exportRootsOf(source.scene);
      const meshes = countExportableMeshes(roots);
      if (meshes === 0) {
        toast({
          tone: 'warning',
          title: 'Nothing to export yet',
          description: 'Draw walls on the plan first; the 3D model is the plan, extruded.',
        });
        return;
      }
      const bytes = await exportGlb(roots);
      saveBlob(new Blob([bytes], { type: 'model/gltf-binary' }), glbFileName(project.name));
      toast({
        tone: 'success',
        title: 'GLB downloaded',
        description: `${String(meshes)} meshes, ${String(Math.round(bytes.byteLength / 1024))} kB — metres, Y-up, for Lumion, D5, Blender or SketchUp.`,
      });
    } catch (err) {
      const problem = AppError.from(err);
      toast({ tone: 'error', title: 'Export did not finish', description: problem.message });
    } finally {
      setBusy(null);
    }
  }, [project.name, source, toast]);

  const exportViaServer = useCallback(async () => {
    setBusy('server');
    try {
      await useJobsStore.getState().startExport(project.id, 'gltf');
      toast({
        tone: 'info',
        title: 'Preparing your 3D model',
        description:
          'The job appears below and carries a Download link when it is ready; the link stays for ten minutes.',
      });
    } catch (err) {
      const problem = AppError.from(err);
      toast({
        tone: 'error',
        title: "Couldn't start the export",
        description: `${problem.message} ${problem.action}`.trim(),
      });
    } finally {
      setBusy(null);
    }
  }, [project.id, toast]);

  return (
    <section
      className={cn(
        'pointer-events-auto flex w-72 flex-col gap-2 rounded-md border border-line bg-surface/95 p-2 shadow-sm backdrop-blur',
        className,
      )}
      aria-label="3D export"
      data-testid="export-panel-3d"
    >
      <div className="flex items-center gap-1.5 px-1">
        <Icon name="cube" size={14} />
        <h2 className="text-xs font-semibold text-ink">Export 3D model</h2>
      </div>
      <div className="flex flex-col gap-1">
        <Button
          size="sm"
          variant="secondary"
          iconLeft="download"
          disabled={busy !== null}
          onClick={() => void downloadThisView()}
          data-testid="export-glb-client"
        >
          {busy === 'client' ? 'Exporting…' : 'Download this view (.glb)'}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          iconLeft="cube"
          disabled={busy !== null}
          onClick={() => void exportViaServer()}
          data-testid="export-glb-server"
        >
          {busy === 'server' ? 'Queuing…' : 'Export from the model (server)'}
        </Button>
      </div>
      <p className="px-1 text-2xs leading-4 text-ink-subtle">
        This view: what is drawn here, facade included. Server: the model itself, one mesh per
        element class. Both in metres, Y-up.
      </p>
      {glbJobs.length > 0 ? (
        <JobList
          jobs={glbJobs}
          onCancel={(id) => void cancel(id)}
          onRetry={(id) => void retry(id)}
          onDismiss={dismiss}
          className="max-h-64 overflow-y-auto"
        />
      ) : null}
    </section>
  );
}
