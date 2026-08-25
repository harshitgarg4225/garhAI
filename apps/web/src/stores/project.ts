/**
 * `project` — the project list, and the one project that is open (§12).
 *
 * This store owns everything about a project EXCEPT its design. The design is
 * an op log and lives in the `model` store, which is its only writer (golden
 * rule 1). What lives here is the metadata around it: the dashboard list, the
 * open project's row, its plot and its brief.
 *
 * Two conventions worth knowing before you read on:
 *
 *  - **Errors are `ProblemDetail`, not `Error`.** State that a component renders
 *    should be a value, not a live exception with a stack and a `cause` chain.
 *    `lib/errors.ts` flattens anything thrown into `{code, message, action}`,
 *    and the shape is structurally the `Problem` that `<ProblemPanel>` takes.
 *  - **The DTO is the page contract.** `pages/_contracts.ts` declares what the
 *    dashboard and the project shell read; this store imports those types rather
 *    than re-describing them, so a change over there is a compile error here
 *    instead of a blank card at runtime.
 *
 * `open()` also kicks off a model hydrate in the background. §15 asks for
 * "open project → interactive canvas <2s", and waiting until the Plan tab
 * mounts to start fetching the op log spends that budget twice.
 */

import { create } from 'zustand';

import { polygonAreaMm2, type UnitsDisplay } from '@garh/model';

import { api } from '../lib/api';
import { AppError, toProblemDetail, type ProblemDetail } from '../lib/errors';
import type { Brief, Plot, Project, ProjectDetail } from '../lib/schemas';
import type {
  CreateProjectPayload,
  ProjectDTO,
  ProjectSlice,
} from '../pages/_contracts';
import type { ProjectStatus } from '../components/types';
import { useJobsStore } from './jobs';
import { useModelStore } from './model';

export type { CreateProjectPayload, ProjectDTO } from '../pages/_contracts';

// ---------------------------------------------------------------------------
// Wire → DTO
// ---------------------------------------------------------------------------

/**
 * `projects.status` has six values in the database (`draft`, `brief`,
 * `options`, `design`, `drawings`, `archived`) and the card shows three. The
 * four working states collapse to `active` because the card already displays
 * where the project actually is — that is what the stage chips are for, and
 * showing "drawings" twice in two vocabularies would be noise.
 */
export function toProjectStatus(raw: string): ProjectStatus {
  if (raw === 'archived') return 'archived';
  if (raw === 'draft') return 'draft';
  return 'active';
}

/** A string field out of the brief's free-form `data`, or undefined. */
function briefString(data: Readonly<Record<string, unknown>>, key: string): string | undefined {
  const value = data[key];
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : undefined;
}

/**
 * An integer field out of the brief's free-form `data`, or undefined.
 *
 * Integer-only on purpose: the model core rejects float values anywhere in
 * `brief.data` (they would break canonical JSON and therefore the state hash),
 * so a float here means the data is already wrong and quietly rounding it would
 * hide that.
 */
function briefInt(data: Readonly<Record<string, unknown>>, ...keys: string[]): number | undefined {
  for (const key of keys) {
    const value = data[key];
    if (typeof value === 'number' && Number.isInteger(value)) return value;
  }
  return undefined;
}

/** `{x, y}` pairs from the plot boundary, area in integer mm². */
function plotAreaOf(plot: Plot | null): number | undefined {
  if (!plot || plot.boundary.length < 3) return undefined;
  return polygonAreaMm2(plot.boundary);
}

/**
 * API row → the DTO the pages render.
 *
 * `detail` is the extra context available once a project is open (`GET
 * /projects/:id` carries the plot and the brief). The list endpoint has neither,
 * so those fields come back undefined and the card degrades honestly rather
 * than guessing.
 */
export function toProjectDTO(
  project: Project,
  detail?: { plot?: Plot | null; brief?: Brief | null },
): ProjectDTO {
  const briefData = detail?.brief?.data ?? {};
  const clientName = project.clientName ?? briefString(briefData, 'clientName');
  const plotArea = project.plotAreaMm2 ?? plotAreaOf(detail?.plot ?? null);
  const storeysAbove = project.storeysAbove ?? briefInt(briefData, 'storeysAbove', 'floorsAbove');
  const bedrooms = project.bedrooms ?? briefInt(briefData, 'bedrooms');

  // The list endpoint may already carry the four dashboard markers. When it does
  // not, the brief's own completeness is still a real signal, so pass it
  // through rather than dropping the whole progress block.
  const progress =
    project.progress ??
    (detail?.brief
      ? {
          briefCompleteness: detail.brief.completeness,
          optionCount: null,
          appliedOptionId: null,
          wallCount: null,
          sheetCount: null,
          sheetsStale: null,
          complianceCheckedAt: null,
        }
      : undefined);

  return {
    id: project.id,
    name: project.name,
    clientName,
    status: toProjectStatus(project.status),
    cityPack: project.cityPack,
    units: project.units,
    demo: project.demo,
    updatedAt: project.updatedAt,
    plotAreaMm2: plotArea ?? null,
    storeysAbove: storeysAbove ?? null,
    bedrooms: bedrooms ?? null,
    thumbnailUrl: project.thumbnailUrl,
    progress,
  };
}

// ---------------------------------------------------------------------------
// The demo plot (§17)
// ---------------------------------------------------------------------------

/** 30 ft in integer mm. `30 × 304.8 = 9144` exactly. */
const DEMO_PLOT_WIDTH_MM = 9144;
/** 40 ft in integer mm. `40 × 304.8 = 12192` exactly. */
const DEMO_PLOT_DEPTH_MM = 12192;

/** CCW with y up — the orientation the model core canonicalises to. */
function rectBoundary(widthMm: number, depthMm: number): { x: number; y: number }[] {
  return [
    { x: 0, y: 0 },
    { x: widthMm, y: 0 },
    { x: widthMm, y: depthMm },
    { x: 0, y: depthMm },
  ];
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

export interface ProjectState extends ProjectSlice {
  /** Full detail for the open project: plot, brief, branch head, latest version. */
  detail: ProjectDetail | null;
  /** True while a mutation (create/rename/archive/units) is in flight. */
  saving: boolean;

  /** Re-fetch the open project without clearing what is on screen. */
  refresh: () => Promise<void>;
  /** Leave the project shell: drops the open project and resets the model store. */
  close: () => void;
  clearError: () => void;

  /** `PUT /projects/:id/plot`. Boundary points are integer mm. */
  savePlot: (
    projectId: string,
    input: {
      boundary?: { x: number; y: number }[];
      northDeg?: number;
      roads?: { edgeIndex: number; widthMm: number | null }[];
      regProfile?: Record<string, unknown>;
      source?: string;
    },
  ) => Promise<Plot>;

  /** `PUT /projects/:id/brief`. `merge` defaults to true — a patch, not a replace. */
  saveBrief: (
    projectId: string,
    input: {
      data?: Record<string, unknown>;
      merge?: boolean;
      vastuMode?: 'off' | 'advisory' | 'strict';
      completeness?: number;
    },
  ) => Promise<Brief>;

  /**
   * The optimistic PATCH that `rename`, `archive` and `setUnits` share.
   * Public because it is the extension point for the next field the project
   * row grows (architect-of-record, city pack); rejects with an `AppError`.
   */
  applyPatch: (
    projectId: string,
    patch: { name?: string; status?: string; units?: UnitsDisplay },
  ) => Promise<void>;
}

/**
 * Cancels the previous `open()` when the user navigates project → project
 * faster than the network answers. Without it the slower response wins and the
 * shell shows the project you just left.
 */
let openController: AbortController | null = null;
let listController: AbortController | null = null;

/** Replace one row in the list, leaving order and the rest untouched. */
function replaceItem(
  items: readonly ProjectDTO[] | undefined,
  next: ProjectDTO,
): ProjectDTO[] | undefined {
  if (!items) return undefined;
  const index = items.findIndex((p) => p.id === next.id);
  if (index === -1) return [next, ...items];
  const copy = items.slice();
  copy[index] = next;
  return copy;
}

export const useProjectStore = create<ProjectState>()((set, get) => ({
  items: undefined,
  loading: false,
  error: null,
  current: null,
  detail: null,
  saving: false,

  // ── The dashboard list ─────────────────────────────────────────────────

  load: async () => {
    listController?.abort();
    const controller = new AbortController();
    listController = controller;

    // Keep whatever is on screen while refetching: §15 wants skeletons on the
    // FIRST load, not a flash of empty on every revisit.
    set({ loading: true, ...(get().items === undefined ? { error: null } : {}) });

    try {
      const page = await api.projects.list({ signal: controller.signal, includeArchived: true });
      if (controller.signal.aborted) return;
      set({ items: page.items.map((p) => toProjectDTO(p)), loading: false, error: null });
    } catch (err) {
      if (controller.signal.aborted) return;
      const error = AppError.from(err);
      if (error.isAborted) return;
      set({ loading: false, error: toProblemDetail(error) });
    } finally {
      if (listController === controller) listController = null;
    }
  },

  // ── Mutations ──────────────────────────────────────────────────────────

  create: async (payload: CreateProjectPayload) => {
    set({ saving: true, error: null });
    try {
      const project = await api.projects.create({
        name: payload.name,
        units: payload.units,
        cityPack: payload.cityPack,
      });

      // The dialog collects the plot and the client name in the same step, but
      // they are separate resources (§11: PUT /plot, PUT /brief). Both are
      // best-effort follow-ups: a project that exists with no plot is a state
      // the app handles, whereas failing the whole create would lose the name
      // the architect just typed.
      let plot: Plot | null = null;
      if (payload.plot) {
        plot = await api.plot
          .put(project.id, {
            boundary: rectBoundary(payload.plot.widthMm, payload.plot.depthMm),
            source: 'manual',
          })
          .catch(() => null);
      }

      let brief: Brief | null = null;
      if (payload.clientName !== undefined && payload.clientName.trim() !== '') {
        brief = await api.brief
          .put(project.id, { data: { clientName: payload.clientName.trim() }, merge: true })
          .catch(() => null);
      }

      const dto = toProjectDTO(project, { plot, brief });
      set((s) => ({
        saving: false,
        items: s.items === undefined ? [dto] : [dto, ...s.items],
      }));
      return dto;
    } catch (err) {
      set({ saving: false });
      // Rethrown, not swallowed: the dialog owns the inline error message, and
      // a store that reports failure only through state cannot tell the caller
      // whether to close.
      throw AppError.from(err);
    }
  },

  rename: async (projectId, name) => {
    const trimmed = name.trim();
    if (trimmed === '') return;
    await get().applyPatch(projectId, { name: trimmed });
  },

  archive: async (projectId) => {
    await get().applyPatch(projectId, { status: 'archived' });
  },

  setUnits: async (projectId, units: UnitsDisplay) => {
    await get().applyPatch(projectId, { units });
  },

  // ── Opening one project ────────────────────────────────────────────────

  open: async (projectId) => {
    const s = get();
    if (s.current?.id === projectId && s.detail?.project.id === projectId) {
      // Already open. Refresh in the background so a stale tab catches up
      // without flashing a skeleton at someone who is already reading it.
      void s.refresh();
      return;
    }

    openController?.abort();
    const controller = new AbortController();
    openController = controller;

    set({ loading: true, error: null, current: null, detail: null });

    try {
      const detail = await api.projects.get(projectId, { signal: controller.signal });
      if (controller.signal.aborted) return;

      const dto = toProjectDTO(detail.project, { plot: detail.plot, brief: detail.brief });
      set((st) => ({
        loading: false,
        error: null,
        current: dto,
        detail,
        items: replaceItem(st.items, dto),
      }));

      // Fire and forget, deliberately. The shell renders as soon as the
      // metadata lands; the op log arriving a beat later is what makes the Plan
      // tab instant when the architect reaches it. A failure here surfaces on
      // the model store's own `loadError`, not as a project-load failure —
      // the project did load.
      const model = useModelStore.getState();
      if (model.projectId !== projectId) {
        void model.hydrate(projectId);
      }

      // Same reasoning for in-flight solver/render jobs: the job cards should
      // already be live when the Renders tab opens.
      void useJobsStore.getState().watchProject(projectId);
    } catch (err) {
      if (controller.signal.aborted) return;
      const error = AppError.from(err);
      if (error.isAborted) return;
      set({ loading: false, error: toProblemDetail(error) });
    } finally {
      if (openController === controller) openController = null;
    }
  },

  refresh: async () => {
    const projectId = get().current?.id;
    if (projectId === undefined) return;
    try {
      const detail = await api.projects.get(projectId);
      const dto = toProjectDTO(detail.project, { plot: detail.plot, brief: detail.brief });
      set((st) => ({
        current: st.current?.id === projectId ? dto : st.current,
        detail: st.current?.id === projectId ? detail : st.detail,
        items: replaceItem(st.items, dto),
      }));
    } catch {
      // A background refresh that fails is not worth interrupting anyone over;
      // the screen keeps showing the last good data. A foreground `open()`
      // reports properly.
    }
  },

  close: () => {
    openController?.abort();
    openController = null;
    const projectId = get().current?.id;
    if (projectId !== undefined) useJobsStore.getState().unwatchProject(projectId);
    useModelStore.getState().reset();
    set({ current: null, detail: null, error: null, loading: false });
  },

  clearError: () => set({ error: null }),

  // ── The seeded demo project (§17, golden rule 8) ────────────────────────

  ensureDemoProject: async () => {
    let items = get().items;
    if (items === undefined) {
      await get().load();
      items = get().items;
    }

    const seeded = (items ?? []).find((p) => p.demo);
    if (seeded) return seeded;

    // The seed script creates the demo project, but a firm that deleted it must
    // still be able to take up the offer on an empty state. What we can build
    // from the client is a real project on the same 30×40 Bengaluru plot — it
    // will not carry `demo: true` (that flag is the server's, and the plan,
    // facade and renders come from the seeder), so the card shows it as an
    // ordinary project. That is the honest outcome: it is one.
    const project = await api.projects.create({
      name: 'Demo — 30 × 40 plot, Bengaluru',
      units: 'ft-in',
      cityPack: 'blr',
    });

    const plot = await api.plot
      .put(project.id, {
        boundary: rectBoundary(DEMO_PLOT_WIDTH_MM, DEMO_PLOT_DEPTH_MM),
        // North up, 9 m road on the south edge — §17's demo plot.
        northDeg: 0,
        roads: [{ edgeIndex: 0, widthMm: 9000 }],
        source: 'manual',
      })
      .catch(() => null);

    const dto = toProjectDTO(project, { plot });
    set((s) => ({ items: s.items === undefined ? [dto] : [dto, ...s.items] }));
    return dto;
  },

  // ── Plot & brief ───────────────────────────────────────────────────────

  savePlot: async (projectId, input) => {
    const plot = await api.plot.put(projectId, input);
    set((s) =>
      s.detail?.project.id === projectId
        ? {
            detail: { ...s.detail, plot },
            current: toProjectDTO(s.detail.project, { plot, brief: s.detail.brief }),
          }
        : {},
    );
    return plot;
  },

  saveBrief: async (projectId, input) => {
    const brief = await api.brief.put(projectId, { merge: true, ...input });
    set((s) =>
      s.detail?.project.id === projectId
        ? {
            detail: { ...s.detail, brief },
            current: toProjectDTO(s.detail.project, { plot: s.detail.plot, brief }),
          }
        : {},
    );
    return brief;
  },

  /**
   * Shared PATCH path for rename / archive / units.
   *
   * Optimistic: the row updates before the request, and reverts if the server
   * refuses. A rename that visibly lags by 300 ms feels broken, and a rename
   * that silently did not happen is worse.
   */
  applyPatch: async (
    projectId: string,
    patch: { name?: string; status?: string; units?: UnitsDisplay },
  ) => {
    const before = get();
    const optimistic = (dto: ProjectDTO): ProjectDTO => ({
      ...dto,
      ...(patch.name === undefined ? {} : { name: patch.name }),
      ...(patch.units === undefined ? {} : { units: patch.units }),
      ...(patch.status === undefined ? {} : { status: toProjectStatus(patch.status) }),
    });

    set((s) => ({
      saving: true,
      items: s.items?.map((p) => (p.id === projectId ? optimistic(p) : p)),
      current: s.current?.id === projectId ? optimistic(s.current) : s.current,
    }));

    try {
      const project = await api.projects.update(projectId, patch);
      const detail = get().detail;
      const dto = toProjectDTO(
        project,
        detail?.project.id === projectId ? { plot: detail.plot, brief: detail.brief } : undefined,
      );
      set((s) => ({
        saving: false,
        items: replaceItem(s.items, dto),
        current: s.current?.id === projectId ? dto : s.current,
      }));
    } catch (err) {
      set({ saving: false, items: before.items, current: before.current });
      throw AppError.from(err);
    }
  },
}));

// ---------------------------------------------------------------------------
// Selectors
// ---------------------------------------------------------------------------

export const selectProjects = (s: ProjectState): readonly ProjectDTO[] | undefined => s.items;
export const selectCurrentProject = (s: ProjectState): ProjectDTO | null => s.current;
export const selectProjectError = (s: ProjectState): ProblemDetail | null => s.error;
export const selectIsLoadingProjects = (s: ProjectState): boolean => s.loading;
/** The open project's plot, or null before it is drawn. */
export const selectPlot = (s: ProjectState): Plot | null => s.detail?.plot ?? null;
export const selectBrief = (s: ProjectState): Brief | null => s.detail?.brief ?? null;
/** Op-log head as the server reported it when the project was opened. */
export const selectServerHeadIdx = (s: ProjectState): number => s.detail?.headIdx ?? -1;
export const selectVersionBranch = (s: ProjectState): string | null =>
  s.detail?.versionBranch ?? null;
/** Display units for the open project, falling back to the app default. */
export const selectUnits = (s: ProjectState): UnitsDisplay => s.current?.units ?? 'ft-in';
export const selectDemoProject = (s: ProjectState): ProjectDTO | null =>
  (s.items ?? []).find((p) => p.demo) ?? null;
