/**
 * Route components.
 *
 * ROUTE SHAPE the web shell should register (react-router-dom 6.26.2). The
 * project tabs are a `:tab` param rather than six sibling paths so the shell
 * can decide which tabs get the tool rail without the router knowing:
 *
 *   /login                          LoginPage
 *   /                               DashboardPage        (auth required)
 *   /projects/:projectId            -> redirect to /brief
 *   /projects/:projectId/:tab       ProjectShell         (auth required)
 *        index / brief              project/BriefPage
 *        plan                       project/PlanPage
 *        3d                         project/ThreeDPage
 *        renders                    project/RendersPage
 *        sheets                     project/SheetsPage
 *        compliance                 project/CompliancePage
 *   /dev/copilot-eval               dev/CopilotEvalLogPage   (DEV builds only)
 *   *                               NotFoundPage
 *
 * ProjectShell renders <Outlet context={ProjectOutletContext}>, so the six tab
 * components must be nested children of the `:tab` route. Lazy-load `plan` and
 * `3d` once the canvas lands — §12 asks for it and they will pull in three.js.
 *
 * Every route body should sit inside `<ErrorBoundary region="…">` (exported
 * from `../components`) so a render-time throw becomes a problem+json panel
 * with a working recovery button rather than a white screen.
 */

export { LoginPage } from './LoginPage';
export type { LoginPageProps } from './LoginPage';

export { DashboardPage } from './DashboardPage';

export { ProjectShell, useProjectOutlet } from './ProjectShell';
export type { ProjectOutletContext } from './ProjectShell';

export { NotFoundPage } from './NotFoundPage';

export { BriefPage } from './project/BriefPage';
export { PlanPage } from './project/PlanPage';
export { ThreeDPage } from './project/ThreeDPage';
export { RendersPage } from './project/RendersPage';
export { SheetsPage } from './project/SheetsPage';
export { CompliancePage } from './project/CompliancePage';

export { useCopilotDecisionLog } from './useCopilotDecisionLog';

/* Dev tooling. `routes.tsx` registers it behind `import.meta.env.DEV`; it is
   exported here so the barrel documents the whole route table. */
export { CopilotEvalLogPage } from './dev/CopilotEvalLogPage';

export {
  cityLabel,
  configurationLabel,
  deriveStages,
  plotDimsLabel,
  toComplianceIssue,
  toJobVM,
  toProjectSummary,
} from './_contracts';
export type {
  ComplianceResultDTO,
  CreateProjectPayload,
  JobDTO,
  JobsSlice,
  OtpRequestResult,
  ProjectDTO,
  ProjectSlice,
  SessionFirm,
  SessionSlice,
  SessionUser,
  UiSlice,
} from './_contracts';
