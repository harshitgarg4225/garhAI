/**
 * The route table (§12: "dashboard → project (tabs: Brief · Plan · 3D ·
 * Renders · Sheets · Compliance). Lazy-load heavy tabs.").
 *
 * ## What is lazy, and why
 *
 * §14 budgets the initial bundle at <1.5 MB gzipped, and `three` +
 * `@react-three/*` is roughly two thirds of that on its own. Every route below
 * except the login screen is a separate chunk:
 *
 *   - **Plan and 3D** pull the R3F canvas. They must never be in the initial
 *     download; `vite.config.ts` also splits `three` into its own chunk so the
 *     two tabs share one copy.
 *   - **Dashboard, project shell, the other four tabs** are small, but a signed
 *     out visitor should not download the project shell to see a login form,
 *     and a user on the Brief tab should not download the sheets viewer.
 *   - **Login stays eager.** It is the cold-start route for anyone without a
 *     session, and a lazy boundary there would put a skeleton in front of the
 *     one screen that has nothing to wait for.
 *
 * ## Suspense and skeletons
 *
 * Every lazy element sits inside a `<Suspense>` whose fallback is shaped like
 * the screen it is standing in for, not a spinner (§15 "skeletons everywhere,
 * never blank, never spinner-only"). The fallbacks live here rather than in the
 * pages because they must render *before* the page's own module has loaded.
 *
 * Every route body also sits inside an `<ErrorBoundary>`, so a render-time
 * throw becomes a problem+json panel with a working recovery button instead of
 * a white screen (golden rule 9).
 *
 * ## The `:tab` param
 *
 * `ProjectShell` reads `useParams().tab`, so the tab has to be a dynamic
 * segment on the route that renders the shell. React Router resolves a
 * parent's params before its children exist, which means a parent whose own
 * path ends in `:tab` cannot have six siblings matching on that segment's
 * value — `useParams()` in a parent returns only the parent's own matches. The
 * index child below is therefore a dispatcher: one route, six lazy components,
 * chosen by the same param the shell reads. Unknown tabs redirect to Brief
 * rather than rendering an empty frame.
 */

import { Suspense, lazy, useMemo } from 'react';
import {
  Navigate,
  createBrowserRouter,
  useLocation,
  useNavigate,
  useParams,
  type RouteObject,
} from 'react-router-dom';

import { Skeleton, SkeletonForm, SkeletonProjectCard, SkeletonRegion } from '@garh/ui';

import { ErrorBoundary } from './components';
import { LoginPage } from './pages/LoginPage';
import { useSessionStore } from './stores/session';

// ---------------------------------------------------------------------------
// Lazy chunks
// ---------------------------------------------------------------------------

const DashboardPage = lazy(async () => ({
  default: (await import('./pages/DashboardPage')).DashboardPage,
}));
const ProjectShell = lazy(async () => ({
  default: (await import('./pages/ProjectShell')).ProjectShell,
}));
const NotFoundPage = lazy(async () => ({
  default: (await import('./pages/NotFoundPage')).NotFoundPage,
}));

const BriefPage = lazy(async () => ({
  default: (await import('./pages/project/BriefPage')).BriefPage,
}));
/**
 * Phase 5: Plan and 3D are ONE component — `PlanPage` hosts both camera modes
 * and reads the `:tab` segment. The single `lazy()` reference matters more
 * than it looks: two wrappers around the same module would still be two
 * component TYPES, and switching tabs would unmount the canvas — a new scene
 * graph, a new picker, a Manifold re-warm-up — where §12's Tab contract wants
 * a projection swap in place. One reference means React reconciles the
 * plan↔3d tab change as a re-render of the mounted editor.
 */
const EditorPage = lazy(async () => ({
  default: (await import('./pages/project/PlanPage')).PlanPage,
}));
const RendersPage = lazy(async () => ({
  default: (await import('./pages/project/RendersPage')).RendersPage,
}));
const SheetsPage = lazy(async () => ({
  default: (await import('./pages/project/SheetsPage')).SheetsPage,
}));
const CompliancePage = lazy(async () => ({
  default: (await import('./pages/project/CompliancePage')).CompliancePage,
}));

/**
 * The §10 eval-log surface. Registered only in dev builds — `import.meta.env.DEV`
 * is statically replaced, so in production the condition is `false`, the route
 * disappears, and the `lazy()` call below is tree-shaken along with the page's
 * whole chunk. (The page also refuses to render outside dev, belt and braces.)
 */
const CopilotEvalLogPage = lazy(async () => ({
  default: (await import('./pages/dev/CopilotEvalLogPage')).CopilotEvalLogPage,
}));

/**
 * The §13 client viewer. Lazy for the same reason as Plan/3D: it mounts the
 * R3F canvas, and a client tapping a WhatsApp link is the LAST person who
 * should wait on `three` before seeing anything.
 */
const ShareViewerPage = lazy(async () => ({
  default: (await import('./pages/share/ShareViewerPage')).ShareViewerPage,
}));

// ---------------------------------------------------------------------------
// Skeleton slots
// ---------------------------------------------------------------------------

/** The dashboard: a header line and a grid of project cards. */
function DashboardSkeleton(): JSX.Element {
  return (
    <SkeletonRegion label="Loading your projects" className="min-h-screen bg-canvas">
      <div className="flex h-topbar items-center gap-3 border-b border-line bg-surface px-4">
        <Skeleton className="h-4 w-28" />
        <Skeleton className="ml-auto h-8 w-32" shape="block" />
      </div>
      <div className="mx-auto grid max-w-6xl gap-4 p-6 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }, (_, i) => (
          <SkeletonProjectCard key={i} />
        ))}
      </div>
    </SkeletonRegion>
  );
}

/**
 * The project shell: top bar, tab strip, body. Deliberately the same geometry
 * as `ProjectShell`'s own loading state, so the handover from "chunk loading"
 * to "project loading" does not move anything on screen.
 */
function ProjectShellSkeleton(): JSX.Element {
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

/** A form-shaped tab body (Brief). */
function FormTabSkeleton(): JSX.Element {
  return (
    <SkeletonRegion label="Loading" className="mx-auto w-full max-w-3xl p-6">
      <SkeletonForm rows={6} />
    </SkeletonRegion>
  );
}

/**
 * A canvas-shaped tab body (Plan, 3D). This is the one fallback that is on
 * screen for a noticeable time — it stands in front of the `three` chunk — so
 * it fills the frame rather than sitting in a card.
 */
function CanvasTabSkeleton(): JSX.Element {
  return (
    <SkeletonRegion label="Loading the drawing canvas" className="h-full w-full p-3">
      <Skeleton className="h-full min-h-64 w-full" shape="block" />
    </SkeletonRegion>
  );
}

/** A list-shaped tab body (Renders, Sheets, Compliance). */
function ListTabSkeleton(): JSX.Element {
  return (
    <SkeletonRegion label="Loading" className="mx-auto flex w-full max-w-5xl flex-col gap-3 p-6">
      {Array.from({ length: 4 }, (_, i) => (
        <Skeleton key={i} className="h-20 w-full" shape="block" />
      ))}
    </SkeletonRegion>
  );
}

/** Full-page fallback while the session is still being restored. */
function BootSkeleton(): JSX.Element {
  return (
    <SkeletonRegion label="Signing you in" className="min-h-screen bg-canvas p-6">
      <div className="mx-auto flex max-w-sm flex-col gap-3 pt-24">
        <Skeleton className="h-10 w-full" shape="block" />
        <Skeleton className="h-4 w-2/3" />
      </div>
    </SkeletonRegion>
  );
}

// ---------------------------------------------------------------------------
// Guards
// ---------------------------------------------------------------------------

/**
 * Gate a route on an authenticated session.
 *
 * The `unknown`/`restoring` case is the one that matters. The refresh
 * credential is an httpOnly cookie, so on a hard reload the app genuinely does
 * not know whether it has a session until `bootstrap()` answers. Redirecting
 * during that window is the classic bug where every reload bounces a signed-in
 * user to the login screen and then back.
 */
function RequireAuth({ children }: { children: JSX.Element }): JSX.Element {
  const status = useSessionStore((s) => s.status);
  const location = useLocation();

  if (status === 'unknown' || status === 'restoring') return <BootSkeleton />;
  if (status === 'authenticated') return children;

  // `state.from` is how the login screen sends you back where you were headed.
  return <Navigate to="/login" replace state={{ from: `${location.pathname}${location.search}` }} />;
}

/** The inverse: `/login` is pointless once you are signed in. */
function RedirectIfSignedIn({ children }: { children: JSX.Element }): JSX.Element {
  const status = useSessionStore((s) => s.status);
  const location = useLocation();
  const from = readFrom(location.state);

  if (status === 'unknown' || status === 'restoring') return <BootSkeleton />;
  if (status === 'authenticated') return <Navigate to={from ?? '/'} replace />;
  return children;
}

/** Read `state.from` without trusting it: only same-origin paths are honoured. */
function readFrom(state: unknown): string | null {
  if (typeof state !== 'object' || state === null) return null;
  const from = (state as { from?: unknown }).from;
  if (typeof from !== 'string') return null;
  // An open-redirect guard, cheap and absolute: a destination must be a path on
  // this app, never `//evil.example` or a scheme.
  return from.startsWith('/') && !from.startsWith('//') ? from : null;
}

// ---------------------------------------------------------------------------
// Route bodies
// ---------------------------------------------------------------------------

function LoginRoute(): JSX.Element {
  const location = useLocation();
  const navigate = useNavigate();
  const from = readFrom(location.state);

  // `RedirectIfSignedIn` would also fire once `status` flips, so this is belt
  // and braces — but it is the path that runs in practice, and going through
  // the router keeps the "signed in → dashboard" transition on the client
  // rather than costing a full document load (§15 micro-speed).
  return <LoginPage onSignedIn={() => navigate(from ?? '/', { replace: true })} />;
}

/** The six project tabs, keyed by the `:tab` segment. See the header note. */
const TAB_COMPONENTS = {
  brief: { Component: BriefPage, Fallback: FormTabSkeleton },
  plan: { Component: EditorPage, Fallback: CanvasTabSkeleton },
  '3d': { Component: EditorPage, Fallback: CanvasTabSkeleton },
  renders: { Component: RendersPage, Fallback: ListTabSkeleton },
  sheets: { Component: SheetsPage, Fallback: ListTabSkeleton },
  compliance: { Component: CompliancePage, Fallback: ListTabSkeleton },
} as const;

export const PROJECT_TABS = Object.keys(TAB_COMPONENTS) as (keyof typeof TAB_COMPONENTS)[];

export function isProjectTab(value: string | undefined): value is keyof typeof TAB_COMPONENTS {
  return value !== undefined && Object.hasOwn(TAB_COMPONENTS, value);
}

function ProjectTabRoute(): JSX.Element {
  const { projectId = '', tab } = useParams<{ projectId: string; tab: string }>();

  // Remounting the boundary per tab means an error on Sheets does not leave the
  // Plan tab wedged behind the same panel.
  const resetKey = useMemo(() => `${projectId}/${tab ?? ''}`, [projectId, tab]);

  if (!isProjectTab(tab)) {
    return <Navigate to={`/projects/${encodeURIComponent(projectId)}/brief`} replace />;
  }

  const { Component, Fallback } = TAB_COMPONENTS[tab];
  return (
    <ErrorBoundary region={`${tab} tab`} resetKey={resetKey}>
      <Suspense fallback={<Fallback />}>
        <Component />
      </Suspense>
    </ErrorBoundary>
  );
}

// ---------------------------------------------------------------------------
// The table
// ---------------------------------------------------------------------------

export const routes: RouteObject[] = [
  {
    path: '/login',
    element: (
      <ErrorBoundary region="sign in">
        <RedirectIfSignedIn>
          <LoginRoute />
        </RedirectIfSignedIn>
      </ErrorBoundary>
    ),
  },

  {
    path: '/',
    element: (
      <ErrorBoundary region="dashboard">
        <RequireAuth>
          <Suspense fallback={<DashboardSkeleton />}>
            <DashboardPage />
          </Suspense>
        </RequireAuth>
      </ErrorBoundary>
    ),
  },

  {
    path: '/projects/:projectId',
    children: [
      // Bare project URL → the first tab. `replace` keeps the redirect out of
      // the back-button history.
      { index: true, element: <Navigate to="brief" replace /> },
      {
        path: ':tab',
        element: (
          <ErrorBoundary region="project shell">
            <RequireAuth>
              <Suspense fallback={<ProjectShellSkeleton />}>
                <ProjectShell />
              </Suspense>
            </RequireAuth>
          </ErrorBoundary>
        ),
        children: [{ index: true, element: <ProjectTabRoute /> }],
      },
    ],
  },

  /*
   * Dev-only tooling. Spread rather than conditionally rendered inside the
   * element so the route object itself does not exist in a production build.
   */
  ...(import.meta.env.DEV
    ? [
        {
          path: '/dev/copilot-eval',
          element: (
            <ErrorBoundary region="copilot eval log">
              <RequireAuth>
                <Suspense fallback={<DashboardSkeleton />}>
                  <CopilotEvalLogPage />
                </Suspense>
              </RequireAuth>
            </ErrorBoundary>
          ),
        } satisfies RouteObject,
      ]
    : []),

  /*
   * `/share/:token` — the read-only client surface (§13). Deliberately OUTSIDE
   * `RequireAuth`: the whole point is a client with no account. The token in
   * the URL is the only credential, and the page hands it straight to the
   * anonymous `api.shareViewer` calls — nothing here touches the session
   * bootstrap, so opening a share link never triggers a refresh-cookie probe.
   */
  {
    path: '/share/:token',
    element: (
      <ErrorBoundary region="shared design">
        <Suspense fallback={<DashboardSkeleton />}>
          <ShareViewerPage />
        </Suspense>
      </ErrorBoundary>
    ),
  },

  {
    path: '*',
    element: (
      <ErrorBoundary region="not found">
        <Suspense fallback={<DashboardSkeleton />}>
          <NotFoundPage />
        </Suspense>
      </ErrorBoundary>
    ),
  },
];

export const router = createBrowserRouter(routes);
