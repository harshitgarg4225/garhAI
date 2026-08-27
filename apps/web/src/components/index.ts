/**
 * Shared app components.
 *
 * Everything in this folder is PRESENTATIONAL: props in, JSX out. No component
 * here imports a Zustand store or the API client. Pages own the data and pass
 * it down. That is what lets these be rendered in a test (or a future
 * storybook) without a running server, and what keeps a change to the API DTOs
 * from rippling through fifteen files.
 *
 * Data shapes live in `./types`.
 */

export { AppShell, ProjectLayout, PageBody, PageHeader } from './AppShell';
export type { AppShellProps, ProjectLayoutProps } from './AppShell';

export { AutosaveBadge } from './AutosaveBadge';
export type { AutosaveBadgeProps, SaveState } from './AutosaveBadge';

export { CanvasPlaceholder } from './CanvasPlaceholder';
export type { CanvasPlaceholderProps } from './CanvasPlaceholder';

export { ComplianceStrip } from './ComplianceStrip';
export type { ComplianceStripProps } from './ComplianceStrip';

export { CreateProjectDialog, CITY_PACK_OPTIONS } from './CreateProjectDialog';
export type {
  CreateProjectDialogProps,
  CreateProjectInput,
  CityPackValue,
} from './CreateProjectDialog';

export { DiffPreview } from './DiffPreview';
export type { DiffPreviewProps } from './DiffPreview';

export { ErrorBoundary, ProblemPanel, toProblem, resolveRecovery } from './ErrorBoundary';
export type {
  ErrorBoundaryProps,
  ProblemPanelProps,
  Recovery,
  RecoveryKind,
} from './ErrorBoundary';

export { Inspector, LengthProperty } from './Inspector';
export type { InspectorProps } from './Inspector';

export { JobCard, JobList } from './JobCard';
export type { JobCardProps } from './JobCard';

export { PresenceChips, presenceInitials, presencePaletteIndex } from './PresenceChips';
export type { PresenceChipsProps, PresenceUser } from './PresenceChips';

export { ProjectCard, ProjectCardSkeleton } from './ProjectCard';
export type { ProjectCardProps } from './ProjectCard';

export { ProjectStageChips, EMPTY_STAGES } from './ProjectStageChips';
export type { ProjectStageChipsProps } from './ProjectStageChips';

export {
  ShareDialog,
  WhatsAppShareButton,
  SHARE_SECTIONS,
  DEFAULT_SHARE_SECTIONS,
  SHARE_EXPIRY_OPTIONS,
} from './ShareDialog';
export type { ShareDialogProps, ShareSection, WhatsAppShareButtonProps } from './ShareDialog';

export { ShortcutsDialog } from './ShortcutsDialog';
export type { ShortcutsDialogProps } from './ShortcutsDialog';

export { SideRail, TOOLS, TOOL_IDS } from './SideRail';
export type { SideRailProps, SnapMode, ToolId } from './SideRail';

export { TopBar } from './TopBar';
export type { TopBarProps, StoreyTab } from './TopBar';

export { UnitsToggle } from './UnitsToggle';
export type { UnitsToggleProps } from './UnitsToggle';

export { PROJECT_STAGES, STAGE_STATES, complianceIssueKey } from './types';
export type {
  ComplianceIssueVM,
  ComplianceResultStatus,
  DiffOpKind,
  DiffOpVM,
  DiffPreviewVM,
  JobKind,
  JobStatus,
  JobVM,
  Problem,
  ProjectStage,
  ProjectStages,
  ProjectStatus,
  ProjectSummaryVM,
  StageState,
} from './types';
