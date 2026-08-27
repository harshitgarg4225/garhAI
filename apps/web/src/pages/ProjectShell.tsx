/**
 * ProjectShell — the §12 panel layout, and the frame every project tab renders
 * inside.
 *
 *   top bar (project · storey tabs · units · copilot · share · generate)
 *   tab strip (Brief · Plan · 3D · Renders · Sheets · Compliance)
 *   left tool rail | tab body | right inspector | copilot rail
 *   bottom compliance chip strip
 *
 * The rail and inspector only appear on the canvas tabs (Plan, 3D). A tool rail
 * next to a form is noise, and §12 puts the inspector next to a *selection*,
 * which only the canvas has.
 *
 * PHASE 6 — the copilot rail:
 *   · `<CopilotPanel>` is mounted on EVERY tab, lazily, and renders `null`
 *     while `ui.copilotOpen` is false. "Widen the kitchen" is as reasonable an
 *     ask from the Brief tab as from the canvas, and the panel's own apply path
 *     is `useModelStore.dispatch` — which does not care which tab is on screen.
 *   · `/` focuses it. The binding is declared in `lib/keymap.ts` with every
 *     other shortcut; the HANDLER is registered here rather than in
 *     `lib/shortcuts.ts` because it belongs to a feature and because `/` should
 *     type a slash on the dashboard (see that file's note).
 *   · The top bar's sparkles button is the same toggle — §15 requires every
 *     keyboard command to have a visible control.
 *
 * PHASE 4 — the shell now drives the canvas rather than describing it:
 *   · the tool rail, the snap mode and the grid are views of the `ui` store,
 *     which is also what the keyboard map writes, so the two cannot disagree;
 *   · the storey tabs are the model's own storeys, and switching one is a
 *     store write the Plan tab re-memoises against (§15 "instant");
 *   · undo/redo are the model store's, next to the autosave badge;
 *   · the inspector is the overlays' real `InspectorPanel`, lazily imported so
 *     three.js stays out of the shell's chunk;
 *   · a compliance chip's "show me" goes through `ui.requestCanvasFocus`,
 *     because the strip is here and the camera is two router levels down.
 *
 * Child tabs read `useProjectOutlet()` (react-router outlet context) rather
 * than re-fetching the project.
 *
 * STORE CONTRACT (see `./_contracts`):
 *   ../stores/session -> useSessionStore : SessionSlice
 *   ../stores/project -> useProjectStore : ProjectSlice
 *   ../stores/jobs    -> useJobsStore    : JobsSlice
 */

import { Suspense, lazy, useEffect, useMemo, useState } from 'react';
import { Link, Outlet, useNavigate, useOutletContext, useParams } from 'react-router-dom';
import { formatPlotArea, type UnitsDisplay } from '@garh/model';
import { Skeleton, SkeletonRegion, TabLinks, useToast } from '@garh/ui';
import type { TabLinkItem } from '@garh/ui';
import {
  ComplianceStrip,
  Inspector,
  ProblemPanel,
  ProjectLayout,
  ShareDialog,
  SideRail,
  TopBar,
  toProblem,
} from '../components';
import type { ComplianceIssueVM, JobVM, SaveState, StoreyTab } from '../components';
import type { ShareSection } from '../components';
/* The panel itself is lazy (below); only the `/` handler is eager, and it is a
   ~60-line module whose whole dependency list is the ui store. */
import { copilotFocusHandler } from '../features/copilot/focus';
import { useSolverJob } from '../features/options';
import { api } from '../lib/api';
import { AppError } from '../lib/errors';
import { useKeyboardMap, type CommandHandlers } from '../lib/keymap';
import { useJobsStore } from '../stores/jobs';
import { useModelStore, selectCanRedo, selectCanUndo } from '../stores/model';
import { useProjectStore } from '../stores/project';
import { useSelectionStore } from '../stores/selection';
import { useSessionStore } from '../stores/session';
import { selectActiveStoreyId, selectSnapMode, useUiStore } from '../stores/ui';
import { cityLabel, configurationLabel, toJobVM } from './_contracts';
import type { ProjectDTO } from './_contracts';
import { useCopilotDecisionLog } from './useCopilotDecisionLog';
import { useLiveCompliance } from './useLiveCompliance';

/**
 * The canvas inspector is lazy on purpose.
 *
 * `CanvasInspector` (Phase 5: the element/facade-component inspector plus, on
 * the 3D tab, the materials panel) imports feature panels from under
 * `features/canvas/**`, and reaching them eagerly would pull the canvas layer
 * (and therefore three) into the SHELL chunk, which loads for the Brief tab
 * too. §14 budgets the initial bundle at 1.5 MB gz and `routes.tsx` splits
 * `three` for exactly this reason. The lazy boundary keeps the shell's chunk
 * honest — which is also why `CanvasInspector` is deliberately NOT exported
 * from the `components` barrel this file imports eagerly.
 */
const CanvasInspector = lazy(async () => ({
  default: (await import('../components/CanvasInspector')).CanvasInspector,
}));

/**
 * The copilot rail is lazy for the same reason, from the other direction: it
 * pulls the shared `DiffPreview` and the mini plan renderer, and most sessions
 * never open it. `<Suspense>` around it has NO fallback on purpose — while the
 * chunk loads the panel is simply not there yet, and a skeleton rail sliding in
 * next to the canvas would be more disruptive than the 100 ms it stands in for.
 */
const CopilotPanel = lazy(async () => ({
  default: (await import('../features/copilot/CopilotPanel')).CopilotPanel,
}));

// ---------------------------------------------------------------------------
// Outlet context — what a tab gets from the shell
// ---------------------------------------------------------------------------

export interface ProjectOutletContext {
  project: ProjectDTO;
  units: UnitsDisplay;
  /** Jobs for this project, already mapped to view models. */
  jobs: readonly JobVM[];
  /** Compliance results for the current version, or `null` before any run. */
  compliance: readonly ComplianceIssueVM[] | null;
  /**
   * A re-check is in flight. The Plan tab's on-canvas markers need this as
   * well as the strip, so it is part of the contract rather than re-derived by
   * calling `useLiveCompliance` a second time (which would double the polling).
   */
  complianceChecking: boolean;
  /** Open the share dialog from inside a tab (renders and sheets both do). */
  openShare: () => void;
  /** Kick off a solver run from inside a tab. */
  generate: () => void;
}

export function useProjectOutlet(): ProjectOutletContext {
  return useOutletContext<ProjectOutletContext>();
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

interface TabDef {
  key: string;
  label: string;
  icon: TabLinkItem['icon'];
  /** Canvas tabs get the tool rail and the inspector. */
  canvas: boolean;
}

const TABS: readonly TabDef[] = [
  { key: 'brief', label: 'Brief', icon: 'lightbulb', canvas: false },
  { key: 'plan', label: 'Plan', icon: 'wall', canvas: true },
  { key: '3d', label: '3D', icon: 'cube', canvas: true },
  { key: 'renders', label: 'Renders', icon: 'image', canvas: false },
  { key: 'sheets', label: 'Sheets', icon: 'sheet', canvas: false },
  { key: 'compliance', label: 'Compliance', icon: 'shield-check', canvas: false },
];

// ---------------------------------------------------------------------------
// Shell
// ---------------------------------------------------------------------------

export function ProjectShell(): JSX.Element {
  const { projectId = '', tab = 'brief' } = useParams<{ projectId: string; tab: string }>();
  const navigate = useNavigate();
  const { toast } = useToast();

  const firm = useSessionStore((s) => s.firm);
  const project = useProjectStore((s) => s.current);
  const loading = useProjectStore((s) => s.loading);
  const error = useProjectStore((s) => s.error);
  const open = useProjectStore((s) => s.open);
  const rename = useProjectStore((s) => s.rename);
  const setUnits = useProjectStore((s) => s.setUnits);

  const jobsByProject = useJobsStore((s) => s.byProject);

  // The autosave badge reads the model store — the op queue is the only thing
  // that knows whether what is on screen is also what the server has (§15).
  // Primitive selectors on purpose: an object selector would re-render the
  // shell on every store write.
  const modelSaveState = useModelStore((s) => s.saveState);
  const modelHeadIdx = useModelStore((s) => s.headIdx);
  const modelPendingCount = useModelStore((s) => s.pending.length);
  /** The engine's one hard precondition. Gates the strip's skeleton state so a
      brief edit on a plotless project does not flash "checking" chips for a
      report that will honestly answer "nothing to check". */
  const hasBoundary = useModelStore((s) => s.doc.plot.boundary.length >= 3);

  // Live compliance: re-fetched (debounced ≤500ms, §14) every time the server
  // confirms an op group — which is what makes "changing city preset
  // re-validates live" true. `null` = nothing evaluated yet, never a pass.
  const { issues: compliance, checking: complianceChecking } = useLiveCompliance(projectId);

  // Phase 4: the tool rail, the snap toggle and the storey tabs are all views
  // of the `ui` store, not local state. They have to be — the keyboard map
  // (V/W/D/…, G, 1/2/3) writes the same values, and a rail holding its own copy
  // would show `select` while the canvas was drawing a wall.
  const activeTool = useUiStore((s) => s.activeTool);
  const setTool = useUiStore((s) => s.setTool);
  const snapMode = useUiStore(selectSnapMode);
  const setSnapMode = useUiStore((s) => s.setSnapMode);
  const activeStoreyId = useUiStore(selectActiveStoreyId);
  const setActiveStorey = useUiStore((s) => s.setActiveStorey);
  const gridVisible = useUiStore((s) => s.canvasLayers.grid);
  const toggleCanvasLayer = useUiStore((s) => s.toggleCanvasLayer);
  const copilotOpen = useUiStore((s) => s.copilotOpen);
  const togglePanel = useUiStore((s) => s.togglePanel);
  const keyboardEnabled = useUiStore((s) => s.keyboardEnabled);

  const storeys = useModelStore((s) => s.doc.house.storeys);
  const selectedIds = useSelectionStore((s) => s.ids);
  const canUndo = useModelStore(selectCanUndo);
  const canRedo = useModelStore(selectCanRedo);
  const house = useModelStore((s) => s.doc.house);

  const [shareOpen, setShareOpen] = useState(false);
  const [shareUrl, setShareUrl] = useState<string | undefined>(undefined);
  const [shareExpiresAt, setShareExpiresAt] = useState<string | undefined>(undefined);
  const [creatingShare, setCreatingShare] = useState(false);
  /** The created link's id — what revoke needs; the URL alone cannot revoke. */
  const [shareId, setShareId] = useState<string | undefined>(undefined);
  const [revokingShare, setRevokingShare] = useState(false);

  const solver = useSolverJob(projectId);
  const setOptionsOpen = useUiStore((s) => s.setOptionsOpen);

  useEffect(() => {
    if (projectId === '') return;
    void open(projectId);
  }, [projectId, open]);

  /**
   * Keep the active storey pointing at a storey that exists.
   *
   * This is the shell's job rather than the Plan tab's because the storey tabs
   * live up here and the 3D tab reads the same value. It runs on hydrate (null
   * → ground floor) and after any edit that removed the storey you were on —
   * a canvas bound to a deleted storey renders nothing and looks broken.
   */
  useEffect(() => {
    if (storeys.length === 0) return;
    if (activeStoreyId !== null && storeys.some((s) => s.id === activeStoreyId)) return;
    setActiveStorey(storeys[0]?.id ?? null);
  }, [storeys, activeStoreyId, setActiveStorey]);

  /**
   * `/` → the copilot. Registered here rather than in `defaultCommandHandlers`
   * (see `lib/shortcuts.ts`); the two maps both listen on `document`, but only
   * one of them has a handler for this command, so it cannot double-fire.
   *
   * The dynamic import is load-bearing, not decoration. `focusCopilotInput`
   * waits two animation frames for the panel's input to register itself, which
   * is plenty once the chunk is in memory and nowhere near enough on the FIRST
   * press, when the chunk is still being fetched — the rail would open and the
   * caret would stay wherever it was. Awaiting the module makes press #1 behave
   * like press #10. Opening the rail happens immediately either way, so there
   * is no window where the keystroke looks ignored.
   */
  const commandHandlers = useMemo<CommandHandlers>(
    () => ({
      'copilot.focus': (event, binding) => {
        useUiStore.getState().setPanel('copilot', true);
        void import('../features/copilot/CopilotPanel').then(() => {
          copilotFocusHandler(event, binding);
        });
      },
    }),
    [],
  );
  useKeyboardMap(commandHandlers, { enabled: keyboardEnabled });

  // The human half of §10's eval log (applied/rejected). Best-effort, and the
  // copilot is fully functional without it — see the hook's header.
  useCopilotDecisionLog(projectId);

  const jobs = useMemo(
    () => (jobsByProject[projectId] ?? []).map(toJobVM),
    [jobsByProject, projectId],
  );

  /** Storey tabs: only the first nine get a digit, matching the §12 map. */
  const storeyTabs = useMemo<StoreyTab[]>(
    () =>
      storeys.map((storey, index) => ({
        id: storey.id,
        label: shortStoreyLabel(storey.name, index),
        shortcut: index < 9 ? String(index + 1) : undefined,
      })),
    [storeys],
  );

  const currentTab = TABS.find((t) => t.key === tab) ?? TABS[0];
  const isCanvasTab = currentTab?.canvas === true;

  // ---- loading ------------------------------------------------------------
  if (error !== null) {
    return (
      <div className="min-h-screen bg-canvas">
        <ProblemPanel
          problem={error}
          onRetry={() => void open(projectId)}
          onNavigate={(to) => navigate(to)}
          fullPage
        />
      </div>
    );
  }

  if (project === null || project.id !== projectId) {
    return (
      <SkeletonRegion label="Opening your project" className="flex h-screen flex-col bg-canvas">
        <div className="flex h-topbar items-center gap-3 border-b border-line bg-surface px-3">
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-7 w-32" shape="block" />
          <span className="ml-auto flex gap-2">
            <Skeleton className="h-7 w-20" shape="block" />
            <Skeleton className="h-7 w-28" shape="block" />
          </span>
        </div>
        <div className="flex h-11 items-center gap-3 border-b border-line bg-surface px-3">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-4 w-16" />
          ))}
        </div>
        <div className="flex-1 p-6">
          <Skeleton className="h-full w-full" shape="block" />
        </div>
      </SkeletonRegion>
    );
  }

  // ---- derived labels -----------------------------------------------------
  const units = project.units;
  const subtitleBits = [
    cityLabel(project.cityPack),
    project.plotAreaMm2 === null || project.plotAreaMm2 === undefined
      ? undefined
      : formatPlotArea(project.plotAreaMm2, units),
    configurationLabel(project),
  ].filter((x): x is string => x !== undefined);

  /**
   * Model-store save state → the badge vocabulary. The store's `pending` is a
   * queue state, not a display state, so it reads as "saving"; `idle` (model
   * not hydrated yet) reads as "saved" because there is nothing unsaved to
   * report. Divergence surfaces through the store's own blocking toast, so the
   * badge shows `error` for it rather than a special word.
   */
  const saveState: SaveState =
    modelSaveState === 'pending' || modelSaveState === 'saving'
      ? 'saving'
      : modelSaveState === 'offline'
        ? 'offline'
        : modelSaveState === 'error'
          ? 'error'
          : 'saved';

  const tabItems: TabLinkItem[] = TABS.map((t) => ({
    key: t.key,
    label: t.label,
    href: `/projects/${project.id}/${t.key}`,
    icon: t.icon,
  }));

  const outletContext: ProjectOutletContext = {
    project,
    units,
    jobs,
    compliance,
    complianceChecking,
    openShare: () => setShareOpen(true),
    generate: () => handleGenerate(),
  };

  function handleGenerate(): void {
    // The options surface lives on the Plan tab (the dashboard's stage map has
    // always said so); Generate takes you there, opens it, and starts a solve
    // unless one is already running — the theater picks the job up either way.
    if (currentTab?.key !== 'plan' && currentTab?.key !== '3d') {
      navigate(`/projects/${projectId}/plan`);
    }
    setOptionsOpen(true);
    if (!solver.isRunning) {
      solver.generate().catch((err: unknown) => {
        const error = AppError.from(err);
        toast({
          severity: 'fail',
          title: "Couldn't start generating",
          description: error.message,
          // The two 409s here are actionable (no plot boundary / no brief
          // rooms) and the fix lives on a specific tab; anything else retries.
          action:
            error.code === 'no_brief_rooms'
              ? { label: 'Open the brief', onClick: () => navigate(`/projects/${projectId}/brief`) }
              : error.code === 'no_plot_boundary'
                ? { label: 'Draw the plot', onClick: () => navigate(`/projects/${projectId}/plan`) }
                : { label: 'Try again', onClick: () => handleGenerate() },
        });
      });
    }
  }

  async function handleCreateShare(input: {
    sections: ShareSection[];
    expiryDays: number;
    canComment: boolean;
  }): Promise<void> {
    setCreatingShare(true);
    try {
      const link = await api.share.create(projectId, {
        sections: input.sections,
        canComment: input.canComment,
        expiresInDays: input.expiryDays,
      });
      // The token appears exactly once (§13 — stored hashed, never shown
      // again), so the dialog STAYS OPEN showing the URL for the architect to
      // copy or WhatsApp; closing it early would lose the link forever.
      setShareId(link.id);
      setShareUrl(link.url ?? undefined);
      setShareExpiresAt(link.expiresAt ?? undefined);
    } catch (err) {
      const problem = toProblem(err);
      toast({
        severity: 'fail',
        title: "Couldn't create the link",
        description: problem.message,
        action: { label: 'Try again', onClick: () => void handleCreateShare(input) },
      });
    } finally {
      setCreatingShare(false);
    }
  }

  async function handleRevokeShare(): Promise<void> {
    if (shareId === undefined) return;
    setRevokingShare(true);
    try {
      await api.share.revoke(shareId);
      setShareId(undefined);
      setShareUrl(undefined);
      setShareExpiresAt(undefined);
      toast({
        severity: 'pass',
        title: 'Link revoked',
        description:
          'It stopped working the moment you clicked — anyone holding it sees a dead link.',
      });
    } catch (err) {
      const problem = toProblem(err);
      toast({
        severity: 'fail',
        title: "Couldn't revoke the link",
        description: problem.message,
        action: { label: 'Try again', onClick: () => void handleRevokeShare() },
      });
    } finally {
      setRevokingShare(false);
    }
  }

  return (
    <>
      <ProjectLayout
        scrollBody={!isCanvasTab}
        topBar={
          <TopBar
            projectName={project.name}
            subtitle={subtitleBits.length === 0 ? undefined : subtitleBits.join(' · ')}
            isDemo={project.demo}
            onBack={() => navigate('/')}
            onRename={(name) => {
              void rename(project.id, name).catch((err: unknown) => {
                const problem = toProblem(err);
                toast({
                  severity: 'fail',
                  title: "Couldn't rename the project",
                  description: problem.message,
                  action: { label: 'Try again', onClick: () => void rename(project.id, name) },
                });
              });
            }}
            units={units}
            onUnitsChange={(next) => {
              void setUnits(project.id, next);
            }}
            saveState={saveState}
            version={modelHeadIdx >= 0 ? modelHeadIdx : undefined}
            pendingCount={modelPendingCount}
            /* §15 "everything undoable, visibly": the buttons and ⌘Z call the
               same store action, so a mouse-only user is never locked out. */
            storeys={isCanvasTab ? storeyTabs : undefined}
            activeStoreyId={activeStoreyId ?? undefined}
            onStoreyChange={setActiveStorey}
            canUndo={canUndo}
            canRedo={canRedo}
            onUndo={() => {
              useModelStore.getState().undo();
            }}
            onRedo={() => {
              useModelStore.getState().redo();
            }}
            copilotOpen={copilotOpen}
            onCopilotToggle={() => togglePanel('copilot')}
            onShare={() => setShareOpen(true)}
            onGenerate={handleGenerate}
            generateLabel="Generate plans"
          />
        }
        tabs={
          <TabLinks
            items={tabItems}
            activeKey={currentTab?.key ?? 'brief'}
            label="Project sections"
            renderLink={({ href, className, children, 'aria-current': ariaCurrent }) => (
              <Link to={href} className={className} aria-current={ariaCurrent}>
                {children}
              </Link>
            )}
          />
        }
        rail={
          isCanvasTab ? (
            <SideRail
              activeTool={activeTool}
              onToolChange={setTool}
              snapMode={snapMode}
              onSnapModeChange={setSnapMode}
              gridVisible={gridVisible}
              onGridToggle={() => toggleCanvasLayer('grid')}
            />
          ) : undefined
        }
        inspector={
          isCanvasTab ? (
            /* The SLOT owns the rail's width. `InspectorPanel` and the 3D
               wrapper inside `CanvasInspector` are `w-full` — correct inside
               this fixed-width column, catastrophic as bare flex items in
               `ProjectLayout`'s row: `w-full` makes their flex-basis the whole
               row, and `main` (flex-basis 0) collapses to 0 px, so the canvas
               is invisible. Executed proof: plan-canvas.spec.ts failed with
               "the 2D canvas surface never mounted" until this wrapper. */
            <div className="h-full w-inspector shrink-0 overflow-hidden border-l border-line bg-surface">
              <Suspense fallback={<Inspector loading />}>
                <CanvasInspector
                  house={house}
                  selectedIds={selectedIds}
                  display={units}
                  threeD={currentTab?.key === '3d'}
                />
              </Suspense>
            </div>
          ) : undefined
        }
        /* Mounted on every tab, and only rendered once `ui.copilotOpen` is
           true — the panel returns null otherwise, so this costs one lazy
           boundary and no layout. Deliberately NOT gated on `isCanvasTab`:
           "rename bedroom 2 to guest bedroom" is a fair ask while reading the
           compliance list, and Apply goes through the model store either way. */
        copilot={
          <Suspense fallback={null}>
            <CopilotPanel />
          </Suspense>
        }
        complianceStrip={
          <ComplianceStrip
            issues={compliance ?? []}
            notRun={compliance === null && !(complianceChecking && hasBoundary)}
            checking={complianceChecking}
            /* The strip lives here and the camera lives inside the Plan tab's
               canvas, with a router `<Outlet>` between them. The request goes
               through the `ui` store rather than a prop chain nobody can
               follow; the Plan page selects the elements and frames them. */
            onSelectElements={(elementIds) => {
              useUiStore.getState().requestCanvasFocus(elementIds);
              if (!isCanvasTab) navigate(`/projects/${project.id}/plan`);
            }}
            onOpenAll={() => navigate(`/projects/${project.id}/compliance`)}
          />
        }
      >
        <Outlet context={outletContext} />
      </ProjectLayout>

      <ShareDialog
        open={shareOpen}
        onOpenChange={setShareOpen}
        projectName={project.name}
        plotSummary={
          project.plotAreaMm2 === null || project.plotAreaMm2 === undefined
            ? undefined
            : formatPlotArea(project.plotAreaMm2, units)
        }
        configuration={configurationLabel(project)}
        firmName={firm?.name}
        shareUrl={shareUrl}
        expiresAt={shareExpiresAt}
        creating={creatingShare}
        onCreate={(input) => void handleCreateShare(input)}
        onRevoke={() => void handleRevokeShare()}
        revoking={revokingShare}
      />

      {/* `loading` is surfaced only as a quiet re-fetch hint; the shell itself
          stays interactive while the project refreshes in the background. */}
      {loading ? (
        <span className="sr-only" role="status">
          Refreshing this project
        </span>
      ) : null}
    </>
  );
}

/**
 * "Ground Floor" → "Ground", "First Floor" → "First". The tabs are 7 rem wide
 * and the word "Floor" is on all of them, so it carries no information; the
 * full name stays in the model and on the drawings.
 */
function shortStoreyLabel(name: string, index: number): string {
  const trimmed = name.replace(/\s*floor\s*$/i, '').trim();
  if (trimmed !== '') return trimmed;
  return index === 0 ? 'Ground' : `Level ${index}`;
}

export default ProjectShell;
