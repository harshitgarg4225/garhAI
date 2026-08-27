/**
 * `/share/:token` — the read-only client surface (§13).
 *
 * An architect sends this link to a CLIENT: someone with no account, no
 * training, and no patience for the studio. Everything here follows from that:
 *
 *   · **The plan is the plan.** The viewer folds the same snapshot + ops the
 *     editor folds (`model.hydrateShared` → the shared model core), so what the
 *     client sees is the design — not a screenshot that could lag it. It
 *     renders through the SAME `CanvasRoot`/`PlanScene` the editor uses; a
 *     second renderer would be a second opinion about the geometry.
 *   · **Read-only is structural, not cosmetic.** No tool controller is mounted,
 *     no keyboard map, no op queue — the model store's share hydration path has
 *     no write machinery to reach. There is nothing to disable because nothing
 *     writable exists on this surface.
 *   · **The scope is the server's.** Tabs render only for the sections the
 *     link grants (`SharedProjectOut.sections`), and every fetch is gated
 *     server-side anyway — hiding a tab here is courtesy, not security.
 *   · **A dead link says so.** Expired/revoked tokens 404; the client gets a
 *     plain explanation and "ask your architect", never a blank screen.
 *
 * Comments (when the link grants them) POST through the same anonymous
 * endpoint, which re-derives project and link ids from the token server-side —
 * nothing in this page's state can widen what the token allows.
 */

import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';

import {
  Badge,
  Button,
  CONTROL_CLASS,
  Card,
  CardBody,
  EmptyState,
  Skeleton,
  SkeletonRegion,
  TabPanel,
  Tabs,
  cn,
  type TabItem,
} from '@garh/ui';

import { CanvasRoot, Grid, OutlinePolyline, watchCanvasTheme } from '../../features/canvas/core';
import type { CanvasCore } from '../../features/canvas/core';
import {
  RoomTagLayer,
  disposeOverlayMaterials,
  refreshOverlayMaterials,
  roomTags,
} from '../../features/canvas/overlays';
import { api } from '../../lib/api';
import { AppError } from '../../lib/errors';
import type { RenderJob, Sheet, SharedProject } from '../../lib/schemas';
import { formatDate, formatDateTime } from '../../lib/units';
import { useModelStore } from '../../stores/model';
import { useSessionStore } from '../../stores/session';
import { useUiStore } from '../../stores/ui';
import {
  PlanScene,
  disposePlanMaterials,
  planExtentMm,
  refreshPlanMaterials,
  storeyFflMm,
} from '../project/plan';

// ---------------------------------------------------------------------------
// Which granted sections this viewer can actually show
// ---------------------------------------------------------------------------

/**
 * A link may grant `three_d` and `compliance`, but the anonymous API exposes
 * viewers only for the plan model, renders and sheets — so those are the tabs.
 * Unknown grants are ignored rather than rendered as dead tabs: the server
 * owns the vocabulary and may extend it before this page learns the word.
 */
const VIEWER_TABS = [
  { key: 'plan', label: 'Plan' },
  { key: 'renders', label: 'Renders' },
  { key: 'sheets', label: 'Drawings' },
] as const;

type ViewerTabKey = (typeof VIEWER_TABS)[number]['key'];

const EMPTY_HIGHLIGHT: readonly string[] = [];

// ---------------------------------------------------------------------------
// The page
// ---------------------------------------------------------------------------

type LoadPhase =
  | { name: 'loading' }
  | { name: 'ready'; project: SharedProject }
  | { name: 'dead'; error: AppError };

export function ShareViewerPage(): JSX.Element {
  const { token = '' } = useParams<{ token: string }>();
  const [phase, setPhase] = useState<LoadPhase>({ name: 'loading' });
  const [tab, setTab] = useState<ViewerTabKey>('plan');
  const enterShareMode = useSessionStore((s) => s.enterShareMode);

  useEffect(() => {
    let cancelled = false;
    setPhase({ name: 'loading' });
    api.shareViewer
      .project(token)
      .then((project) => {
        if (cancelled) return;
        enterShareMode({ token, sections: project.sections, canComment: project.canComment });
        setPhase({ name: 'ready', project });
        const first = VIEWER_TABS.find((t) => project.sections.includes(t.key));
        if (first !== undefined) setTab(first.key);
        // Start folding the plan immediately — it is the tab a client opens for.
        if (project.sections.includes('plan')) {
          void useModelStore.getState().hydrateShared(token);
        }
      })
      .catch((err: unknown) => {
        const error = AppError.from(err);
        if (error.isAborted || cancelled) return;
        setPhase({ name: 'dead', error });
      });
    return () => {
      cancelled = true;
    };
  }, [token, enterShareMode]);

  // A guest leaves the model store as they found it (StrictMode double-mounts
  // re-run the load effect above, so resetting on cleanup is safe).
  useEffect(() => () => useModelStore.getState().reset(), []);

  if (phase.name === 'loading') {
    return (
      <SkeletonRegion label="Opening the shared design" className="min-h-screen bg-canvas">
        <div className="flex h-topbar items-center gap-3 border-b border-line bg-surface px-4">
          <Skeleton className="h-4 w-40" />
          <Skeleton className="ml-auto h-5 w-20" shape="block" />
        </div>
        <div className="p-4">
          <Skeleton className="h-[60vh] w-full" shape="block" />
        </div>
      </SkeletonRegion>
    );
  }

  if (phase.name === 'dead') {
    return (
      <main className="flex min-h-screen items-center justify-center bg-canvas p-6">
        <EmptyState
          icon="share"
          title="This link is no longer available"
          description={
            phase.error.status === 404 || phase.error.status === 410
              ? 'It may have expired or been turned off. Ask the architect who sent it for a fresh link.'
              : phase.error.message
          }
          demoAction={{
            notApplicable:
              'A share link is scoped to one design; there is nothing else to open here.',
          }}
        />
      </main>
    );
  }

  const { project } = phase;
  const granted = VIEWER_TABS.filter((t) => project.sections.includes(t.key));
  const items: TabItem<ViewerTabKey>[] = granted.map((t) => ({ value: t.key, label: t.label }));

  return (
    // h-screen, not min-h-screen: the canvas sizes itself with percentage
    // heights, which only resolve down a chain of DEFINITE heights. The studio
    // pages inherit that from the app shell; this page is its own shell.
    <div className="flex h-screen flex-col bg-canvas">
      <header className="flex h-topbar shrink-0 items-center gap-3 border-b border-line bg-surface px-4">
        <h1 className="truncate text-sm font-semibold text-ink">{project.projectName}</h1>
        <Badge tone="neutral">View only</Badge>
        <span className="ml-auto hidden text-xs text-ink-muted sm:block">
          {project.updatedAt !== null ? `Updated ${formatDateTime(project.updatedAt)}` : null}
          {project.updatedAt !== null && project.expiresAt !== null ? ' · ' : null}
          {project.expiresAt !== null ? `Link expires ${formatDate(project.expiresAt)}` : null}
        </span>
      </header>

      {granted.length === 0 ? (
        <main className="flex flex-1 items-center justify-center p-6">
          <EmptyState
            icon="share"
            title="Nothing is shared on this link"
            description="The architect who sent it can grant the plan, renders or drawings from their share settings."
            demoAction={{ notApplicable: 'A share link only shows what it was scoped to.' }}
          />
        </main>
      ) : (
        <main className="flex min-h-0 flex-1 flex-col">
          {granted.length > 1 ? (
            <div className="border-b border-line bg-surface px-4">
              <Tabs items={items} value={tab} onValueChange={setTab} label="Shared sections" />
            </div>
          ) : null}

          {granted.some((t) => t.key === 'plan') ? (
            <TabPanel value="plan" active={tab === 'plan'} className="flex min-h-0 flex-1 flex-col">
              <SharePlanView projectName={project.projectName} units={project.units} />
            </TabPanel>
          ) : null}
          {granted.some((t) => t.key === 'renders') ? (
            <TabPanel
              value="renders"
              active={tab === 'renders'}
              className="min-h-0 flex-1 overflow-y-auto"
            >
              <ShareRendersView token={token} />
            </TabPanel>
          ) : null}
          {granted.some((t) => t.key === 'sheets') ? (
            <TabPanel
              value="sheets"
              active={tab === 'sheets'}
              className="min-h-0 flex-1 overflow-y-auto"
            >
              <ShareSheetsView token={token} />
            </TabPanel>
          ) : null}

          {project.canComment ? <ShareCommentComposer token={token} /> : null}
        </main>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Plan — the real model through the real canvas, minus every write path
// ---------------------------------------------------------------------------

function SharePlanView({
  projectName,
  units,
}: {
  projectName: string;
  units: SharedProject['units'];
}): JSX.Element {
  const status = useModelStore((s) => s.status);
  const loadError = useModelStore((s) => s.loadError);
  const house = useModelStore((s) => s.doc.house);
  const plotBoundary = useModelStore((s) => s.doc.plot.boundary);
  const activeStoreyId = useUiStore((s) => s.activeStoreyId);
  const setActiveStorey = useUiStore((s) => s.setActiveStorey);
  const [core, setCore] = useState<CanvasCore | null>(null);

  // Same theming/GPU-cleanup contract the Plan page holds: this is the other
  // place that mounts the plan + overlay material sets.
  useEffect(() => {
    const stop = watchCanvasTheme(() => {
      refreshOverlayMaterials();
      refreshPlanMaterials();
    });
    return () => {
      stop();
      disposeOverlayMaterials();
      disposePlanMaterials();
    };
  }, []);

  const elevationMm = useMemo(() => storeyFflMm(house, activeStoreyId), [house, activeStoreyId]);
  const tags = useMemo(
    () => (activeStoreyId === null ? [] : roomTags(house.rooms, activeStoreyId, units)),
    [house.rooms, activeStoreyId, units],
  );

  // Frame the drawing whenever the storey changes — a viewer has no drawing
  // hand to yank the camera from, so refitting on switch is the kind thing.
  useEffect(() => {
    if (core === null || status !== 'ready') return;
    const extent = planExtentMm(house, activeStoreyId, plotBoundary);
    if (extent !== null) core.viewport.fitBbox(extent, { animate: false });
  }, [core, status, house, activeStoreyId, plotBoundary]);

  if (status === 'error' && loadError !== null) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <EmptyState
          icon="info"
          title="The plan did not load"
          description={loadError.message}
          demoAction={{ notApplicable: 'Reload the page to try again.' }}
        />
      </div>
    );
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      {house.storeys.length > 1 ? (
        <div
          role="tablist"
          aria-label="Storeys"
          className="flex shrink-0 items-center gap-1 border-b border-line bg-surface px-4 py-1.5"
        >
          {house.storeys.map((storey) => (
            <button
              key={storey.id}
              type="button"
              role="tab"
              aria-selected={storey.id === activeStoreyId}
              onClick={() => setActiveStorey(storey.id)}
              className={cn(
                'garh-focus-ring rounded px-2 py-1 text-xs font-medium',
                storey.id === activeStoreyId
                  ? 'bg-brand-soft text-brand-ink'
                  : 'text-ink-muted hover:text-ink',
              )}
            >
              {storey.name}
            </button>
          ))}
        </div>
      ) : null}

      <div className="relative min-h-0 flex-1">
        {status !== 'ready' ? (
          <SkeletonRegion label="Loading the plan" className="absolute inset-0">
            <Skeleton className="h-full w-full" shape="block" />
          </SkeletonRegion>
        ) : (
          <CanvasRoot
            mode="2d"
            activeStoreyId={activeStoreyId}
            planeElevationMm={elevationMm}
            navigation
            hover={false}
            ariaLabel={`Plan of ${projectName} (view only)`}
            onCoreReady={setCore}
          >
            <Grid visible />
            {plotBoundary.length >= 3 ? (
              <OutlinePolyline
                pointsMm={plotBoundary}
                elevationMm={elevationMm}
                tone="preview"
                closed
                dashed
                layer="grid"
              />
            ) : null}
            <PlanScene house={house} storeyId={activeStoreyId} elevationMm={elevationMm} />
            <RoomTagLayer
              tags={tags}
              elevationMm={elevationMm}
              storeyId={activeStoreyId}
              highlightIds={EMPTY_HIGHLIGHT}
              visible
            />
          </CanvasRoot>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Renders — finished images only (the server already filters to succeeded)
// ---------------------------------------------------------------------------

function ShareRendersView({ token }: { token: string }): JSX.Element {
  const [state, setState] = useState<
    { name: 'loading' } | { name: 'ready'; jobs: RenderJob[] } | { name: 'error'; error: AppError }
  >({ name: 'loading' });

  useEffect(() => {
    let cancelled = false;
    api.shareViewer
      .renders(token)
      .then((jobs) => {
        if (!cancelled) setState({ name: 'ready', jobs });
      })
      .catch((err: unknown) => {
        const error = AppError.from(err);
        if (!error.isAborted && !cancelled) setState({ name: 'error', error });
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (state.name === 'loading') {
    return (
      <SkeletonRegion
        label="Loading renders"
        className="grid gap-4 p-4 sm:grid-cols-2 lg:grid-cols-3"
      >
        <Skeleton className="aspect-video w-full" shape="block" />
        <Skeleton className="aspect-video w-full" shape="block" />
        <Skeleton className="aspect-video w-full" shape="block" />
      </SkeletonRegion>
    );
  }
  if (state.name === 'error') {
    return (
      <div className="flex justify-center p-6">
        <EmptyState
          icon="info"
          title="The renders did not load"
          description={state.error.message}
          demoAction={{ notApplicable: 'Reload the page to try again.' }}
        />
      </div>
    );
  }
  if (state.jobs.length === 0) {
    return (
      <div className="flex justify-center p-6">
        <EmptyState
          icon="image"
          title="No renders yet"
          description="When the architect renders this design, the images will appear here."
          demoAction={{ notApplicable: 'Renders are produced in the studio, not on this page.' }}
        />
      </div>
    );
  }
  return (
    <ul className="grid gap-4 p-4 sm:grid-cols-2 lg:grid-cols-3">
      {state.jobs.map((job) => (
        <li key={job.id}>
          <Card>
            {job.outputUrl !== null ? (
              // The URL is re-presigned per request (§13, ≤10 min) — render it
              // immediately, never cache it in state beyond this list.
              <img
                src={job.outputUrl}
                alt={`${job.mode} render`}
                className="aspect-video w-full rounded-t-lg object-cover"
                loading="lazy"
              />
            ) : null}
            <CardBody className="flex items-center justify-between py-2 text-xs text-ink-muted">
              <span className="capitalize">{job.mode}</span>
              <span>{formatDateTime(job.createdAt)}</span>
            </CardBody>
          </Card>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Drawings — sheet metadata; the files themselves stay with the architect
// ---------------------------------------------------------------------------

function ShareSheetsView({ token }: { token: string }): JSX.Element {
  const [state, setState] = useState<
    { name: 'loading' } | { name: 'ready'; sheets: Sheet[] } | { name: 'error'; error: AppError }
  >({ name: 'loading' });

  useEffect(() => {
    let cancelled = false;
    api.shareViewer
      .sheets(token)
      .then((set) => {
        if (!cancelled) setState({ name: 'ready', sheets: [...set.sheets] });
      })
      .catch((err: unknown) => {
        const error = AppError.from(err);
        if (!error.isAborted && !cancelled) setState({ name: 'error', error });
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (state.name === 'loading') {
    return (
      <SkeletonRegion label="Loading the drawing list" className="space-y-3 p-4">
        <Skeleton className="h-16 w-full" shape="block" />
        <Skeleton className="h-16 w-full" shape="block" />
      </SkeletonRegion>
    );
  }
  if (state.name === 'error') {
    return (
      <div className="flex justify-center p-6">
        <EmptyState
          icon="info"
          title="The drawing list did not load"
          description={state.error.message}
          demoAction={{ notApplicable: 'Reload the page to try again.' }}
        />
      </div>
    );
  }
  if (state.sheets.length === 0) {
    return (
      <div className="flex justify-center p-6">
        <EmptyState
          icon="sheet"
          title="No drawings yet"
          description="When the architect generates the drawing set, the sheet list will appear here."
          demoAction={{ notApplicable: 'Drawings are generated in the studio, not on this page.' }}
        />
      </div>
    );
  }
  return (
    <div className="p-4">
      <ul className="space-y-3">
        {state.sheets.map((sheet) => (
          <li key={sheet.id}>
            <Card>
              <CardBody className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-3">
                {sheet.number !== null ? (
                  <span className="font-mono text-xs text-ink-muted">{sheet.number}</span>
                ) : null}
                <span className="text-sm font-medium text-ink">{sheet.title ?? sheet.kind}</span>
                {sheet.scaleDenominator !== null ? (
                  <span className="text-xs text-ink-muted">1:{sheet.scaleDenominator}</span>
                ) : null}
                {sheet.generatedAt !== null ? (
                  <span className="ml-auto text-xs text-ink-muted">
                    {formatDateTime(sheet.generatedAt)}
                  </span>
                ) : null}
              </CardBody>
            </Card>
          </li>
        ))}
      </ul>
      <p className="mt-4 text-xs text-ink-muted">
        This link lists the set; the PDF and DXF files come from your architect (§13 — downloads are
        not part of a share link).
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Comments — the one thing a client can *send*
// ---------------------------------------------------------------------------

function ShareCommentComposer({ token }: { token: string }): JSX.Element {
  const [authorName, setAuthorName] = useState('');
  const [body, setBody] = useState('');
  const [state, setState] = useState<
    { name: 'idle' } | { name: 'sending' } | { name: 'sent' } | { name: 'error'; error: AppError }
  >({ name: 'idle' });

  const submit = (): void => {
    if (body.trim().length === 0 || authorName.trim().length === 0) return;
    setState({ name: 'sending' });
    api.shareViewer
      .comment(token, { body: body.trim(), authorName: authorName.trim() })
      .then(() => {
        setState({ name: 'sent' });
        setBody('');
      })
      .catch((err: unknown) => {
        const error = AppError.from(err);
        if (!error.isAborted) setState({ name: 'error', error });
      });
  };

  return (
    <section
      aria-label="Send feedback to the architect"
      className="shrink-0 border-t border-line bg-surface px-4 py-3"
    >
      <form
        className="flex flex-wrap items-start gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <input
          type="text"
          value={authorName}
          onChange={(e) => setAuthorName(e.target.value)}
          placeholder="Your name"
          aria-label="Your name"
          required
          maxLength={120}
          className={cn(CONTROL_CLASS, 'w-40')}
        />
        <input
          type="text"
          value={body}
          onChange={(e) => {
            setBody(e.target.value);
            if (state.name === 'sent' || state.name === 'error') setState({ name: 'idle' });
          }}
          placeholder="Tell the architect what you think…"
          aria-label="Your comment"
          required
          maxLength={4000}
          className={cn(CONTROL_CLASS, 'min-w-48 flex-1')}
        />
        <Button type="submit" loading={state.name === 'sending'}>
          Send
        </Button>
      </form>
      <p className="mt-1 min-h-4 text-xs" role="status">
        {state.name === 'sent' ? (
          <span className="text-ink-muted">Sent — your architect will see it in the studio.</span>
        ) : state.name === 'error' ? (
          <span className="text-fail-ink">{state.error.message}</span>
        ) : null}
      </p>
    </section>
  );
}
