/**
 * _contracts.ts — WHAT THE PAGES NEED FROM THE STORES AND THE API CLIENT.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 *  READ THIS IF YOU OWN apps/web/src/stores/** OR apps/web/src/lib/**
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * The pages in this folder are the only place in the UI layer that touches
 * application state. They import hooks by these exact names:
 *
 *     import { useSessionStore } from '../stores/session';
 *     import { useProjectStore } from '../stores/project';
 *     import { useJobsStore }    from '../stores/jobs';
 *     import { useUiStore }      from '../stores/ui';
 *
 * …and the interfaces below are the slices they read. This file declares them
 * so the coupling is written down in one place instead of being discovered by a
 * type error in nine files. The store owner should make each store satisfy the
 * matching interface (structurally — no import of this file is required, though
 * `satisfies SessionSlice` is a cheap way to keep them honest).
 *
 * Naming follows playbook §12, which names the stores `session`, `project`,
 * `model`, `selection`, `jobs`, `ui`. `model` and `selection` are not used by
 * any Phase-0 page — the canvas (Phase 4) is their first consumer.
 *
 * The DTO types below describe what `lib/api` is expected to return. Pages map
 * DTO → view model with the helpers at the bottom of this file, so a change in
 * the wire format is a change in one function, not in every component.
 */

import { formatFtIn, formatMetres, type UnitsDisplay } from '@garh/model';
import type {
  ComplianceIssueVM,
  JobKind,
  JobStatus,
  JobVM,
  Problem,
  ProjectStages,
  ProjectStatus,
  ProjectSummaryVM,
  StageState,
} from '../components/types';

// ═══════════════════════════════════════════════════════════════════════════
// Session store
// ═══════════════════════════════════════════════════════════════════════════

export interface SessionUser {
  id: string;
  email: string;
  name: string;
  role: 'admin' | 'member';
}

export interface SessionFirm {
  id: string;
  name: string;
  logoUrl?: string | null | undefined;
}

/** Result of `POST /auth/otp`. */
export interface OtpRequestResult {
  /** Seconds until the code expires (the API uses 10 minutes, §13). */
  expiresInSeconds: number;
  /** Seconds before "Send again" should be enabled. */
  resendAfterSeconds: number;
  /**
   * DEV ONLY. When `PROVIDER_EMAIL=mock` the API echoes the code back instead
   * of sending mail, so the whole product is usable with no SMTP configured.
   * The login page surfaces this honestly, labelled as a development
   * convenience — it must be `undefined` in staging and production.
   */
  devCode?: string | undefined;
}

export interface SessionSlice {
  /** `authenticating` covers both the OTP request and the verify round-trip. */
  status: 'unknown' | 'anonymous' | 'authenticating' | 'authenticated';
  user: SessionUser | null;
  firm: SessionFirm | null;
  requestOtp: (email: string) => Promise<OtpRequestResult>;
  verifyOtp: (email: string, code: string) => Promise<void>;
  signOut: () => Promise<void>;
}

// ═══════════════════════════════════════════════════════════════════════════
// Project store
// ═══════════════════════════════════════════════════════════════════════════

/** What `GET /projects` returns per row. */
export interface ProjectDTO {
  id: string;
  name: string;
  clientName?: string | null | undefined;
  status: ProjectStatus;
  cityPack?: string | null | undefined;
  units: UnitsDisplay;
  demo: boolean;
  updatedAt: string;
  /** Integer mm², or null before the plot exists. */
  plotAreaMm2?: number | null | undefined;
  /** Storeys above ground, e.g. 1 for G+1. */
  storeysAbove?: number | null | undefined;
  bedrooms?: number | null | undefined;
  thumbnailUrl?: string | null | undefined;
  /**
   * Progress markers the API derives (brief completeness, whether options
   * exist, whether sheets are stale). Optional so the dashboard degrades to
   * "todo" chips rather than failing.
   */
  progress?:
    | {
        briefCompleteness?: number | null | undefined;
        optionCount?: number | null | undefined;
        appliedOptionId?: string | null | undefined;
        wallCount?: number | null | undefined;
        sheetCount?: number | null | undefined;
        sheetsStale?: boolean | null | undefined;
        complianceCheckedAt?: string | null | undefined;
      }
    | undefined;
}

export interface CreateProjectPayload {
  name: string;
  clientName?: string | undefined;
  cityPack: string;
  units: UnitsDisplay;
  /** Both integer mm. Sent only when the architect filled the plot step. */
  plot?: { widthMm: number; depthMm: number } | undefined;
}

export interface ProjectSlice {
  /** Undefined until the first load resolves. */
  items: readonly ProjectDTO[] | undefined;
  loading: boolean;
  /** problem+json, never a raw Error. */
  error: Problem | null;
  /** The currently open project, when inside the project shell. */
  current: ProjectDTO | null;

  load: () => Promise<void>;
  create: (payload: CreateProjectPayload) => Promise<ProjectDTO>;
  rename: (projectId: string, name: string) => Promise<void>;
  archive: (projectId: string) => Promise<void>;
  setUnits: (projectId: string, units: UnitsDisplay) => Promise<void>;
  open: (projectId: string) => Promise<void>;
  /**
   * Ensures the seeded demo project exists for this firm and returns it. The
   * seed script creates it, but a firm that deleted it should still be able to
   * take up the offer on an empty state (golden rule 8).
   */
  ensureDemoProject: () => Promise<ProjectDTO>;
}

// ═══════════════════════════════════════════════════════════════════════════
// Jobs store
// ═══════════════════════════════════════════════════════════════════════════

export interface JobDTO {
  id: string;
  kind: JobKind;
  status: JobStatus;
  progress?: number | null | undefined;
  /** The worker's own stage message — §15 forbids inventing these. */
  message?: string | null | undefined;
  queuePosition?: number | null | undefined;
  createdAt?: string | null | undefined;
  error?: Problem | null | undefined;
}

export interface JobsSlice {
  /** Active + recently finished jobs for the open project. */
  byProject: Readonly<Record<string, readonly JobDTO[]>>;
  cancel: (jobId: string) => Promise<void>;
  retry: (jobId: string) => Promise<void>;
  dismiss: (jobId: string) => void;
}

// ═══════════════════════════════════════════════════════════════════════════
// UI store
// ═══════════════════════════════════════════════════════════════════════════

export interface UiSlice {
  theme: 'light' | 'dark' | 'system';
  setTheme: (theme: 'light' | 'dark' | 'system') => void;
  /** Whether the first-run coach-mark tour has been completed (§15). */
  tourDone: boolean;
  setTourDone: (done: boolean) => void;
}

// ═══════════════════════════════════════════════════════════════════════════
// DTO → view model
// ═══════════════════════════════════════════════════════════════════════════

const CITY_LABELS: Readonly<Record<string, string>> = {
  blr: 'Bengaluru',
  ncr: 'Delhi NCR',
  hyd: 'Hyderabad',
  custom: 'Custom rules',
};

export function cityLabel(cityPack: string | null | undefined): string | undefined {
  if (cityPack === null || cityPack === undefined || cityPack === '') return undefined;
  return CITY_LABELS[cityPack] ?? cityPack.toUpperCase();
}

/** "G+1 · 3 BHK" — the one-line configuration summary. */
export function configurationLabel(dto: ProjectDTO): string | undefined {
  const bits: string[] = [];
  const above = dto.storeysAbove ?? null;
  if (above !== null) bits.push(above === 0 ? 'Ground floor' : `G+${above}`);
  const beds = dto.bedrooms ?? null;
  if (beds !== null && beds > 0) bits.push(`${beds} BHK`);
  return bits.length === 0 ? undefined : bits.join(' · ');
}

/**
 * Derive the four F10 dashboard chips.
 *
 * The rules are deliberately conservative: a stage only reads `done` when we
 * have positive evidence, so a project whose API row lacks `progress` shows
 * four honest "todo" chips rather than an optimistic guess.
 */
export function deriveStages(dto: ProjectDTO): ProjectStages {
  const p = dto.progress;
  const briefCompleteness = p?.briefCompleteness ?? 0;
  const optionCount = p?.optionCount ?? 0;
  const wallCount = p?.wallCount ?? 0;
  const sheetCount = p?.sheetCount ?? 0;
  const sheetsStale = p?.sheetsStale === true;

  const brief: StageState =
    briefCompleteness >= 80 ? 'done' : briefCompleteness > 0 ? 'active' : 'todo';

  const options: StageState =
    (p?.appliedOptionId ?? null) !== null ? 'done' : optionCount > 0 ? 'active' : 'todo';

  // "Design" is done once there is geometry AND an option has been settled on;
  // walls without a chosen option means hand-drawing is in progress.
  const design: StageState =
    wallCount === 0 ? 'todo' : (p?.appliedOptionId ?? null) !== null ? 'done' : 'active';

  const drawings: StageState = sheetCount === 0 ? 'todo' : sheetsStale ? 'stale' : 'done';

  return { brief, options, design, drawings };
}

export function toProjectSummary(dto: ProjectDTO): ProjectSummaryVM {
  return {
    id: dto.id,
    name: dto.name,
    clientName: dto.clientName ?? undefined,
    cityPack: dto.cityPack ?? undefined,
    cityLabel: cityLabel(dto.cityPack),
    plotAreaMm2: dto.plotAreaMm2 ?? null,
    configuration: configurationLabel(dto),
    status: dto.status,
    stages: deriveStages(dto),
    updatedAt: dto.updatedAt,
    unitsDisplay: dto.units,
    isDemo: dto.demo,
    thumbnailUrl: dto.thumbnailUrl ?? undefined,
  };
}

export function toJobVM(dto: JobDTO): JobVM {
  return {
    id: dto.id,
    kind: dto.kind,
    status: dto.status,
    progress: dto.progress ?? null,
    stageMessage: dto.message ?? undefined,
    queuePosition: dto.queuePosition ?? null,
    startedAt: dto.createdAt ?? undefined,
    error: dto.error ?? undefined,
  };
}

/** Rules-engine result row as the API returns it. */
export interface ComplianceResultDTO {
  ruleId: string;
  status: 'pass' | 'warn' | 'fail' | 'not_applicable';
  message: string;
  cite?: string | null | undefined;
  confidence?: 'seed' | 'reviewed' | 'verified' | null | undefined;
  elements?: readonly string[] | null | undefined;
  fixHint?: string | null | undefined;
  /**
   * True when the pack's `autofix` block yields an applicable op group.
   *
   * The API field is `fixAvailable` (see `RuleResult.to_json()` in
   * apps/api/garh_rules/results.py). It was read here as `autofixAvailable`,
   * which is always undefined, so "Fix it" never appeared on any chip.
   */
  fixAvailable?: boolean | null | undefined;
}

export function toComplianceIssue(dto: ComplianceResultDTO): ComplianceIssueVM {
  return {
    ruleId: dto.ruleId,
    status: dto.status,
    message: dto.message,
    cite: dto.cite ?? undefined,
    confidence: dto.confidence ?? undefined,
    elementIds: dto.elements ?? [],
    fixAvailable: dto.fixAvailable === true,
    fixHint: dto.fixHint ?? undefined,
  };
}

/**
 * "30'-0" × 40'-0"" for the project subtitle. Kept here rather than in a
 * component because the same string appears in the top bar and in the WhatsApp
 * share message, and they must not drift.
 */
export function plotDimsLabel(
  widthMm: number | null | undefined,
  depthMm: number | null | undefined,
  units: UnitsDisplay,
): string | undefined {
  if (widthMm === null || widthMm === undefined || depthMm === null || depthMm === undefined) {
    return undefined;
  }
  const fmt =
    units === 'ft-in' ? (mm: number) => formatFtIn(mm, { dropZeroInches: true }) : formatMetres;
  return `${fmt(widthMm)} × ${fmt(depthMm)}`;
}
