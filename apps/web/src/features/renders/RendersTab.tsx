/**
 * RendersTab.tsx — the Renders tab (§9): history grid pinned to design
 * versions, the stale banner, re-render with the same recipe, the client-pack
 * queue with its zip download, and "Share on WhatsApp" (§15).
 *
 * This tab owns no scene, so it never captures. "New render", "Client pack"
 * and "Re-render" write a pending request into the feature store and jump to
 * the 3D view, where `RenderLauncher` photographs the live model and starts
 * the job — the only §9-honest way to render from a tab without a canvas.
 *
 * Staleness is SERVER truth: the ops pipeline flips `stale=true` on every
 * visual edit, and this tab merely shows the flag ("Design changed since this
 * render"). No client-side guessing — a wrongly-fresh image costs the
 * architect's credibility (§9).
 */

import { useCallback, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Badge, Button, Icon, Spinner, cn } from '@garh/ui';

import { JobList, PageBody } from '../../components';
import { toJobVM } from '../../pages/_contracts';
import { AppError } from '../../lib/errors';
import { selectJobsFor, useJobsStore } from '../../stores/jobs';
import { useUiStore } from '../../stores/ui';
import { useProjectOutlet } from '../../pages/ProjectShell';
import { archiveRenderPack, type RenderJob } from './api';
import { ReferenceBoard } from '../references';
import { MODE_COPY, PRESETS_BY_ID, RENDER_PRESETS, randomSeed } from './presets';
import { useRendersUiStore } from './store';
import { useRenderHistory } from './useRenderHistory';
import { renderShareMessage, waShareUrl } from './whatsapp';

export function RendersTab(): JSX.Element {
  const navigate = useNavigate();
  const { project, openShare } = useProjectOutlet();
  const history = useRenderHistory(project.id);
  const activeJobs = useJobsStore(selectJobsFor(project.id)).filter(
    (job) => job.kind === 'render' && (job.status === 'queued' || job.status === 'running'),
  );

  const goCapture = useCallback(
    (request: Parameters<ReturnType<typeof useRendersUiStore.getState>['requestRender']>[0]) => {
      useRendersUiStore.getState().requestRender(request);
      navigate(`/projects/${project.id}/3d`);
    },
    [navigate, project.id],
  );

  // Group history into packs (shared params.packId) and singles.
  const { singles, packs } = useMemo(() => groupHistory(history.items), [history.items]);

  return (
    <PageBody className="max-w-5xl">
      {/* ── header: the two ways to make images ─────────────────────────── */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-base font-semibold text-ink">Renders</h1>
          <p className="mt-0.5 text-xs text-ink-muted">
            Photoreal images from your model — captured from the 3D view, pinned to the design
            version they were made from.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            iconLeft="layers"
            onClick={() => goCapture({ kind: 'pack', seed: randomSeed() })}
          >
            Client pack (8)
          </Button>
          <Button
            variant="primary"
            size="sm"
            iconLeft="image"
            onClick={() =>
              goCapture({
                kind: 'single',
                preset: 'exterior-street-day',
                mode: 'precise',
                seed: randomSeed(),
              })
            }
          >
            New render
          </Button>
        </div>
      </div>

      {/* ── Precise vs Explore, in plain words (§9, Forma's contract) ───── */}
      <div className="mt-4 grid gap-2 sm:grid-cols-2">
        {(['precise', 'explore'] as const).map((mode) => (
          <div key={mode} className="rounded-lg border border-line bg-surface p-3">
            <p className="text-xs font-semibold text-ink">{MODE_COPY[mode].title}</p>
            <p className="mt-1 text-2xs leading-4 text-ink-muted">{MODE_COPY[mode].body}</p>
          </div>
        ))}
      </div>

      {/* ── the inspiration board (§11) ──────────────────────────────────
          Placed above the queue and the history, because it is what a render
          READS. A board tucked away in its own tab would be annotated once and
          forgotten, and the review's questions have to be in front of the
          architect at the moment they are about to make an image. */}
      <ReferenceBoard projectId={project.id} presets={RENDER_PRESETS} className="mt-5" />

      {/* ── live queue (real events only — §15) ─────────────────────────── */}
      {activeJobs.length > 0 ? (
        <section className="mt-5" aria-label="Renders in progress">
          <h2 className="mb-2 text-sm font-semibold text-ink">In progress</h2>
          <JobList jobs={activeJobs.map(toJobVM)} />
        </section>
      ) : null}

      {/* ── packs ───────────────────────────────────────────────────────── */}
      {packs.length > 0 ? (
        <section className="mt-5" aria-label="Client packs">
          <h2 className="mb-2 text-sm font-semibold text-ink">Client packs</h2>
          <div className="space-y-3">
            {packs.map((pack) => (
              <PackCard
                key={pack.packId}
                projectId={project.id}
                projectName={project.name}
                pack={pack}
              />
            ))}
          </div>
        </section>
      ) : null}

      {/* ── history grid ────────────────────────────────────────────────── */}
      <section className="mt-5" aria-label="Render history">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-ink">History</h2>
          <Button variant="ghost" size="sm" iconLeft="refresh" onClick={history.refresh}>
            Refresh
          </Button>
        </div>

        {history.error !== null ? (
          <p
            className="rounded-md border border-line bg-surface p-3 text-xs text-ink-muted"
            role="alert"
          >
            {history.error} — try Refresh.
          </p>
        ) : null}

        {history.loading && history.items.length === 0 ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-hidden="true">
            {[0, 1, 2].map((i) => (
              <div
                key={i}
                className="aspect-[3/2] animate-pulse rounded-lg border border-line bg-surface-sunken"
              />
            ))}
          </div>
        ) : null}

        {!history.loading && singles.length === 0 && packs.length === 0 ? (
          <div className="rounded-lg border border-dashed border-line p-8 text-center">
            <Icon name="image" size={24} className="mx-auto text-ink-muted" aria-hidden="true" />
            <p className="mt-2 text-sm font-semibold text-ink">No renders yet</p>
            <p className="mx-auto mt-1 max-w-sm text-xs leading-5 text-ink-muted">
              Open the 3D view and press <strong>Render</strong> — a Precise render follows your
              model exactly; the one-click client pack shoots six exteriors plus the living room and
              kitchen.
            </p>
            <div className="mt-3">
              <Button
                variant="secondary"
                size="sm"
                onClick={() =>
                  goCapture({
                    kind: 'single',
                    preset: 'exterior-street-day',
                    mode: 'precise',
                    seed: randomSeed(),
                  })
                }
              >
                Render the 3D view
              </Button>
            </div>
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {singles.map((job) => (
              <RenderCard
                key={job.id}
                job={job}
                projectName={project.name}
                onRerender={(recipe) => goCapture(recipe)}
              />
            ))}
          </div>
        )}

        {history.hasMore ? (
          <div className="mt-3 flex justify-center">
            <Button variant="ghost" size="sm" onClick={history.loadMore} disabled={history.loading}>
              Load more
            </Button>
          </div>
        ) : null}
      </section>

      <div className="mt-6 border-t border-line pt-3">
        <Button variant="ghost" size="sm" iconLeft="share" onClick={openShare}>
          Share a read-only link with your client
        </Button>
      </div>
    </PageBody>
  );
}

// ---------------------------------------------------------------------------
// One render
// ---------------------------------------------------------------------------

function RenderCard({
  job,
  projectName,
  onRerender,
}: {
  job: RenderJob;
  projectName: string;
  onRerender: (recipe: {
    kind: 'single';
    preset: string;
    mode: 'precise' | 'explore';
    seed: number;
  }) => void;
}): JSX.Element {
  const preset = String(job.params.preset ?? 'exterior-street-day');
  const seed = Number(job.params.seed ?? 0);
  const label = PRESETS_BY_ID.get(preset)?.label ?? preset;

  return (
    <figure className="overflow-hidden rounded-lg border border-line bg-surface">
      <div className="relative aspect-[3/2] bg-surface-sunken">
        {job.status === 'succeeded' && job.outputUrl !== null ? (
          <img
            src={job.outputUrl}
            alt={`${label}, ${job.mode} render`}
            className="h-full w-full object-cover"
            loading="lazy"
          />
        ) : (
          <div className="flex h-full items-center justify-center text-2xs text-ink-muted">
            {job.status === 'failed'
              ? (job.error ?? 'This render failed.')
              : `Render ${job.status}…`}
          </div>
        )}
        {/* §9: "Design changed since this render" */}
        {job.stale ? (
          <p className="absolute inset-x-0 top-0 bg-amber-500/90 px-2 py-1 text-2xs font-medium text-black">
            Design changed since this render
          </p>
        ) : null}
      </div>
      <figcaption className="flex items-center justify-between gap-2 px-2.5 py-2">
        <div className="min-w-0">
          <p className="truncate text-xs font-medium text-ink">{label}</p>
          <p className="text-2xs text-ink-muted garh-nums">
            {job.mode} · seed {seed}
            {job.designVersionId === null ? '' : ' · pinned to version'}
          </p>
          {/* §11: which board references this image actually followed. Named
              rather than counted, and taken from the server's own record of what
              the prompt consumed — "did it use my reference?" is a question an
              architect asks about a finished picture, not a checkbox. */}
          {job.referencesUsed.length > 0 ? (
            <p className="truncate text-2xs text-ink-subtle">
              Followed {job.referencesUsed.map((r) => r.label).join(', ')}
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {job.status === 'succeeded' && job.outputUrl !== null ? (
            <>
              <a
                href={waShareUrl(renderShareMessage(projectName, job.outputUrl))}
                target="_blank"
                rel="noreferrer noopener"
                className="rounded p-1 text-ink-muted hover:text-ink"
                aria-label="Share on WhatsApp"
                title="Share on WhatsApp"
              >
                <Icon name="share" size={14} aria-hidden="true" />
              </a>
              <a
                href={job.outputUrl}
                download
                className="rounded p-1 text-ink-muted hover:text-ink"
                aria-label="Download image"
                title="Download"
              >
                <Icon name="download" size={14} aria-hidden="true" />
              </a>
            </>
          ) : null}
          {/* Re-render carries the SAME preset + seed forward (§9). */}
          <button
            type="button"
            onClick={() =>
              onRerender({
                kind: 'single',
                preset,
                mode: job.mode,
                seed: Number.isFinite(seed) ? seed : 0,
              })
            }
            className="rounded p-1 text-ink-muted hover:text-ink"
            aria-label={`Re-render ${label} with the same settings`}
            title="Re-render (same style and seed, current design)"
          >
            <Icon name="refresh" size={14} aria-hidden="true" />
          </button>
        </div>
      </figcaption>
    </figure>
  );
}

// ---------------------------------------------------------------------------
// One pack
// ---------------------------------------------------------------------------

interface PackGroup {
  readonly packId: string;
  readonly jobs: readonly RenderJob[];
}

function PackCard({
  projectId,
  projectName,
  pack,
}: {
  projectId: string;
  projectName: string;
  pack: PackGroup;
}): JSX.Element {
  const [zipping, setZipping] = useState(false);
  const done = pack.jobs.filter((j) => j.status === 'succeeded').length;
  const failed = pack.jobs.filter((j) => j.status === 'failed').length;
  const total = pack.jobs.length;
  const complete = done === total;
  const stale = pack.jobs.some((j) => j.stale);

  const download = useCallback(async () => {
    setZipping(true);
    try {
      const archive = await archiveRenderPack(projectId, pack.packId);
      if (archive.downloadUrl !== null) {
        window.open(archive.downloadUrl, '_blank', 'noopener');
        useUiStore.getState().pushToast({
          tone: 'success',
          title: 'Pack zip ready',
          description: 'The download link is valid for 10 minutes.',
          action: {
            label: 'Share on WhatsApp',
            run: () =>
              window.open(
                waShareUrl(renderShareMessage(projectName, archive.downloadUrl ?? '')),
                '_blank',
                'noopener',
              ),
          },
        });
      }
    } catch (err) {
      const problem = AppError.from(err);
      useUiStore.getState().pushToast({
        tone: problem.code === 'render_pack_not_ready' ? 'warning' : 'error',
        title: 'Zip not ready',
        description: problem.message,
      });
    } finally {
      setZipping(false);
    }
  }, [projectId, projectName, pack.packId]);

  return (
    <div className="rounded-lg border border-line bg-surface p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Icon name="layers" size={16} className="text-ink-muted" aria-hidden="true" />
          <span className="text-sm font-medium text-ink">Client pack</span>
          <Badge tone={complete ? 'pass' : failed > 0 ? 'fail' : 'neutral'}>
            {complete ? 'ready' : `${done}/${total} done${failed > 0 ? `, ${failed} failed` : ''}`}
          </Badge>
          {stale ? <Badge tone="warn">design changed since</Badge> : null}
        </div>
        <div className="flex items-center gap-2">
          {zipping ? <Spinner size={14} label="Preparing zip" /> : null}
          <Button
            variant="secondary"
            size="sm"
            iconLeft="download"
            disabled={!complete || zipping}
            onClick={() => void download()}
            title={
              complete ? 'Download all images as one zip' : 'Available when every image is done'
            }
          >
            Download zip
          </Button>
        </div>
      </div>

      {/* Per-shot strip: thumbnails as they land, honest states meanwhile. */}
      <ul className="mt-2 flex gap-1.5 overflow-x-auto pb-1" aria-label="Pack images">
        {pack.jobs.map((job) => {
          const slug = String(job.params.packSlug ?? job.params.preset ?? 'shot');
          return (
            <li key={job.id} className="shrink-0">
              {job.status === 'succeeded' && job.outputUrl !== null ? (
                <img
                  src={job.outputUrl}
                  alt={slug}
                  title={slug}
                  className="h-14 w-20 rounded border border-line object-cover"
                  loading="lazy"
                />
              ) : (
                <div
                  className={cn(
                    'flex h-14 w-20 items-center justify-center rounded border border-dashed border-line text-2xs',
                    job.status === 'failed' ? 'text-red-500' : 'text-ink-muted',
                  )}
                  title={`${slug}: ${job.status}`}
                >
                  {job.status === 'failed' ? 'failed' : `${job.progress}%`}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Grouping
// ---------------------------------------------------------------------------

function groupHistory(items: readonly RenderJob[]): {
  singles: RenderJob[];
  packs: PackGroup[];
} {
  const singles: RenderJob[] = [];
  const byPack = new Map<string, RenderJob[]>();
  for (const job of items) {
    const packId = job.params.packId;
    if (typeof packId === 'string' && packId !== '') {
      const list = byPack.get(packId) ?? [];
      list.push(job);
      byPack.set(packId, list);
    } else {
      singles.push(job);
    }
  }
  const packs: PackGroup[] = [...byPack.entries()].map(([packId, jobs]) => ({
    packId,
    jobs: jobs
      .slice()
      .sort((a, b) => Number(a.params.packIndex ?? 0) - Number(b.params.packIndex ?? 0)),
  }));
  return { singles, packs };
}

export default RendersTab;
