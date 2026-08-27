/**
 * RenderLauncher.tsx — the "photograph this model" panel on the 3D view.
 *
 * Capture must happen where the scene lives (§9: the capture set comes from
 * the live renderer; a second canvas is forbidden), so the launcher is mounted
 * by PlanPage's 3D overlay. The Renders tab asks for work by writing a pending
 * request into `useRendersUiStore` and navigating here; the launcher runs it
 * on mount — that is the whole cross-tab contract.
 *
 * Every job it starts is handed to the EXISTING jobs store (`track`), so SSE
 * progress, queue position, cancel and retry all ride Phase 0's machinery.
 */

import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { useNavigate } from 'react-router-dom';

import { Button, Icon, cn } from '@garh/ui';

import { AppError } from '../../lib/errors';
import { useJobsStore } from '../../stores/jobs';
import { useModelStore } from '../../stores/model';
import { useUiStore } from '../../stores/ui';
import { useProjectOutlet } from '../../pages/ProjectShell';
import {
  deliverCaptureSet,
  startClientPack,
  startRender,
  toTrackableJob,
  type PackShotUpload,
  type StartRenderInput,
} from './api';
import { captureSet } from './capture';
import { PresetCameraError, presetCamera } from './cameras';
import { captureSource, subscribeCaptureSource, type CaptureSource } from './captureBridge';
import {
  CLIENT_PACK_SHOTS,
  DEFAULT_PRESET_ID,
  DEFAULT_RENDER_SIZE,
  MODE_COPY,
  PRESETS_BY_ID,
  RENDER_PRESETS,
  randomSeed,
  type RenderMode,
} from './presets';
import { useRendersUiStore, type PendingRenderRequest } from './store';

type Busy =
  | { readonly step: 'idle' }
  | { readonly step: 'capturing'; readonly detail: string }
  | { readonly step: 'submitting' };

export function RenderLauncher({ className }: { className?: string }): JSX.Element | null {
  const navigate = useNavigate();
  const { project } = useProjectOutlet();
  const source = useSyncExternalStore(subscribeCaptureSource, captureSource, () => null);

  const [open, setOpen] = useState(false);
  const [presetId, setPresetId] = useState(DEFAULT_PRESET_ID);
  const [mode, setMode] = useState<RenderMode>('precise');
  const [seed, setSeed] = useState<number>(() => randomSeed());
  const [useCurrentView, setUseCurrentView] = useState(false);
  const [busy, setBusy] = useState<Busy>({ step: 'idle' });

  const preset = PRESETS_BY_ID.get(presetId);
  // Interiors are Explore-only at MVP (spec F6); the toggle follows the preset.
  useEffect(() => {
    if (preset !== undefined && !preset.modes.includes(mode)) {
      setMode(preset.modes[0] ?? 'explore');
    }
  }, [preset, mode]);

  const toast = useCallback(
    (input: Parameters<ReturnType<typeof useUiStore.getState>['pushToast']>[0]) => {
      useUiStore.getState().pushToast(input);
    },
    [],
  );

  // ── one shot ─────────────────────────────────────────────────────────────
  const runSingle = useCallback(
    async (
      live: CaptureSource,
      request: { preset: string; mode: RenderMode; seed: number },
      throughCurrentCamera: boolean,
    ) => {
      const house = useModelStore.getState().doc.house;
      setBusy({ step: 'capturing', detail: 'Photographing your model…' });
      try {
        const view = throughCurrentCamera
          ? {
              camera: live.camera,
              viewMeta: { preset: request.preset, fovDeg: 0, eyeMm: null, targetMm: null },
            }
          : presetCamera(request.preset, house);
        const captured = captureSet(live.gl, live.scene, view.camera, DEFAULT_RENDER_SIZE);

        setBusy({ step: 'submitting' });
        const input: StartRenderInput = {
          projectId: project.id,
          mode: request.mode,
          preset: request.preset,
          seed: request.seed,
          width: DEFAULT_RENDER_SIZE.width,
          height: DEFAULT_RENDER_SIZE.height,
          view: view.viewMeta as unknown as Record<string, unknown>,
          // Presigned upload when storage is reachable, inline otherwise.
          inputs: await deliverCaptureSet(project.id, captured),
        };
        const job = await startRender(input);
        useJobsStore
          .getState()
          .track(project.id, toTrackableJob(job), async () =>
            toTrackableJob(await startRender(input)),
          );
        toast({
          tone: 'success',
          title: 'Render started',
          description: `${PRESETS_BY_ID.get(request.preset)?.label ?? request.preset} · ${request.mode} · seed ${request.seed}`,
          action: { label: 'View renders', run: () => navigate(`/projects/${project.id}/renders`) },
        });
      } catch (err) {
        if (err instanceof PresetCameraError) {
          toast({ tone: 'warning', title: 'Nothing to photograph', description: err.message });
        } else {
          const problem = AppError.from(err);
          toast({ tone: 'error', title: 'Render not started', description: problem.message });
        }
      } finally {
        setBusy({ step: 'idle' });
      }
    },
    [project.id, navigate, toast],
  );

  // ── the client pack (§9: one job group) ─────────────────────────────────
  const runPack = useCallback(
    async (live: CaptureSource, baseSeed: number) => {
      const house = useModelStore.getState().doc.house;
      const shots: PackShotUpload[] = [];
      const skipped: string[] = [];
      try {
        for (let i = 0; i < CLIENT_PACK_SHOTS.length; i += 1) {
          const shot = CLIENT_PACK_SHOTS[i];
          if (shot === undefined) continue;
          setBusy({
            step: 'capturing',
            detail: `Photographing ${i + 1} of ${CLIENT_PACK_SHOTS.length}…`,
          });
          // Yield a frame so the counter actually paints between captures.
          await new Promise((resolve) => setTimeout(resolve, 0));
          try {
            const view = presetCamera(shot.preset, house);
            const captured = captureSet(live.gl, live.scene, view.camera, DEFAULT_RENDER_SIZE);
            // Uploaded per shot: 24 inline PNGs would blow the API body cap.
            shots.push({
              slug: shot.slug,
              preset: shot.preset,
              mode: shot.mode,
              view: view.viewMeta as unknown as Record<string, unknown>,
              inputs: await deliverCaptureSet(project.id, captured),
            });
          } catch (err) {
            if (err instanceof PresetCameraError) {
              // An interior without its room: skip that shot, say so, keep going.
              skipped.push(PRESETS_BY_ID.get(shot.preset)?.label ?? shot.preset);
            } else {
              throw err;
            }
          }
        }
        if (shots.length === 0) {
          toast({
            tone: 'warning',
            title: 'Nothing to photograph',
            description: 'Draw walls (and name a living room and kitchen) first.',
          });
          return;
        }

        setBusy({ step: 'submitting' });
        const pack = await startClientPack({
          projectId: project.id,
          seed: baseSeed,
          width: DEFAULT_RENDER_SIZE.width,
          height: DEFAULT_RENDER_SIZE.height,
          shots,
        });
        useRendersUiStore.getState().notePack(pack.packId);
        for (const job of pack.jobs) {
          useJobsStore.getState().track(project.id, toTrackableJob(job));
        }
        toast({
          tone: 'success',
          title: `Client pack started — ${pack.jobs.length} images`,
          description:
            skipped.length === 0
              ? 'Watch the queue on the Renders tab; the zip is one click when they finish.'
              : `Skipped (no room found): ${skipped.join(', ')}.`,
          action: { label: 'View queue', run: () => navigate(`/projects/${project.id}/renders`) },
        });
      } catch (err) {
        const problem = AppError.from(err);
        toast({ tone: 'error', title: 'Pack not started', description: problem.message });
      } finally {
        setBusy({ step: 'idle' });
      }
    },
    [project.id, navigate, toast],
  );

  // ── pending request from the Renders tab ────────────────────────────────
  useEffect(() => {
    if (source === null) return undefined;
    const pending: PendingRenderRequest | null = useRendersUiStore.getState().takePending();
    if (pending === null) return undefined;
    setOpen(true);
    let cancelled = false;
    void (async () => {
      // The 3D layers mount in the same commit that mounted this launcher, and
      // the extrusion pipeline may still be meshing. Two frames + a beat is
      // enough for the demand loop to settle before we photograph it.
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      await new Promise((resolve) => setTimeout(resolve, 250));
      if (cancelled) return;
      if (pending.kind === 'single') {
        setPresetId(pending.preset);
        setMode(pending.mode);
        setSeed(pending.seed);
        await runSingle(source, pending, false);
      } else {
        await runPack(source, pending.seed);
      }
    })();
    return () => {
      cancelled = true;
    };
    // The pending request is a one-shot handoff; deliberately keyed on source
    // only, so it runs exactly once when the scene becomes available.
  }, [source, runSingle, runPack]);

  if (source === null) return null;
  const working = busy.step !== 'idle';

  if (!open) {
    return (
      <div className={cn('pointer-events-auto', className)}>
        <Button variant="primary" size="sm" iconLeft="image" onClick={() => setOpen(true)}>
          Render
        </Button>
      </div>
    );
  }

  return (
    <div
      className={cn(
        'pointer-events-auto w-80 rounded-lg border border-line bg-surface shadow-lg',
        className,
      )}
      role="dialog"
      aria-label="Start a render"
    >
      <div className="flex items-center justify-between border-b border-line px-3 py-2">
        <span className="text-sm font-semibold text-ink">Render</span>
        <Button
          variant="ghost"
          size="sm"
          iconLeft="x"
          onClick={() => setOpen(false)}
          aria-label="Close render panel"
        />
      </div>

      <div className="max-h-[60vh] space-y-3 overflow-y-auto p-3">
        {/* Presets */}
        <div className="grid grid-cols-2 gap-1.5" role="radiogroup" aria-label="Render style">
          {RENDER_PRESETS.map((item) => (
            <button
              key={item.id}
              type="button"
              role="radio"
              aria-checked={item.id === presetId}
              onClick={() => setPresetId(item.id)}
              className={cn(
                'flex items-center gap-2 rounded-md border px-2 py-1.5 text-left text-xs',
                item.id === presetId
                  ? 'border-brand bg-brand-soft text-brand-ink'
                  : 'border-line text-ink-muted hover:border-line-strong',
              )}
            >
              <span
                aria-hidden="true"
                className="h-4 w-4 shrink-0 rounded-full border border-line"
                style={{
                  background: `linear-gradient(135deg, ${item.tint}, ${item.tintSecondary})`,
                }}
              />
              <span className="truncate">{item.label}</span>
            </button>
          ))}
        </div>

        {/* Precise vs Explore — §9 in plain words */}
        <div className="space-y-1.5" role="radiogroup" aria-label="Render mode">
          {(['precise', 'explore'] as const).map((m) => {
            const allowed = preset?.modes.includes(m) ?? true;
            return (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={mode === m}
                disabled={!allowed}
                onClick={() => setMode(m)}
                className={cn(
                  'w-full rounded-md border px-2.5 py-2 text-left',
                  mode === m
                    ? 'border-brand bg-brand-soft'
                    : 'border-line hover:border-line-strong',
                  !allowed && 'cursor-not-allowed opacity-50',
                )}
              >
                <span className="block text-xs font-semibold text-ink">{MODE_COPY[m].title}</span>
                <span className="mt-0.5 block text-2xs leading-4 text-ink-muted">
                  {allowed ? MODE_COPY[m].body : 'Interior views are Explore-only for now.'}
                </span>
              </button>
            );
          })}
        </div>

        {/* Seed + camera */}
        <div className="flex items-center gap-2">
          <label className="flex flex-1 items-center gap-1.5 text-xs text-ink-muted">
            Seed
            <input
              type="number"
              min={0}
              value={seed}
              onChange={(e) => setSeed(Math.max(0, Math.trunc(Number(e.target.value) || 0)))}
              className="w-full rounded-md border border-line bg-surface px-2 py-1 text-xs text-ink garh-nums"
              aria-label="Render seed"
            />
          </label>
          <Button
            variant="ghost"
            size="sm"
            iconLeft="refresh"
            onClick={() => setSeed(randomSeed())}
          >
            New
          </Button>
        </div>
        <label className="flex items-center gap-2 text-xs text-ink-muted">
          <input
            type="checkbox"
            checked={useCurrentView}
            onChange={(e) => setUseCurrentView(e.target.checked)}
          />
          Use my current camera angle (instead of the preset&rsquo;s)
        </label>

        {working ? (
          <p className="flex items-center gap-2 text-xs text-ink-muted" role="status">
            <Icon name="clock" size={14} aria-hidden="true" />
            {busy.step === 'capturing' ? busy.detail : 'Starting the job…'}
          </p>
        ) : null}
      </div>

      <div className="flex items-center justify-between gap-2 border-t border-line px-3 py-2">
        <Button
          variant="secondary"
          size="sm"
          iconLeft="layers"
          disabled={working}
          onClick={() => void runPack(source, seed)}
        >
          Client pack (8)
        </Button>
        <Button
          variant="primary"
          size="sm"
          iconLeft="image"
          disabled={working}
          onClick={() => void runSingle(source, { preset: presetId, mode, seed }, useCurrentView)}
        >
          Start render
        </Button>
      </div>
    </div>
  );
}

export default RenderLauncher;
