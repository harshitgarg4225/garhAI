/**
 * SheetsTab.tsx — the municipal drawing set (F7-A, playbook §7 and Phase 8).
 *
 * Five things live on this screen, in the order an architect uses them:
 *
 *   1. **Generate / regenerate**, with honest job state driven by real worker events.
 *      No synthesised progress: the bar moves when a sheet is actually drawn (§15).
 *   2. **The set** — thumbnails of the real drawings, with the §7 step-5 invariant
 *      ("every chain sums exactly") reported as a fact, not assumed.
 *   3. **The viewer**, zoomable, for reading a dimension string.
 *   4. **Downloads**: PDF set, DXF, glTF, PNG pack — each an export job ending in a
 *      short-lived signed URL, and each honest about a format the server cannot make.
 *   5. **The title block** and **the review tray** (D13).
 *
 * The empty state teaches: it names the six sheets and offers the demo project, per
 * golden rule 8.
 *
 * Nothing here fakes anything. If the worker skipped a sheet, the reason is printed.
 * If a format is unavailable the button says why and what to do instead. If the tray
 * has not re-checked anchors this request, it says "as last checked".
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { Badge, Button, Card, EmptyState, Icon, Spinner, cn, useToast } from '@garh/ui';

import { JobList, PageBody } from '../../components';
import { AppError } from '../../lib/errors';
import { api } from '../../lib/api';
import { useProjectOutlet } from '../../pages/ProjectShell';
import { useJobsStore } from '../../stores/jobs';
import {
  EXPORT_OPTIONS,
  SHEET_KIND_INFO,
  fetchReviewTray,
  fetchSheetSet,
  fetchSheetSummary,
  sheetDownloadLink,
  type ExportKind,
  type ReviewTray as ReviewTrayData,
  type Sheet,
  type SheetSetSummary,
} from './api';
import { ReviewTray } from './ReviewTray';
import { SheetThumbnail } from './SheetThumbnail';
import { SheetViewer } from './SheetViewer';
import { TitleBlockEditor } from './TitleBlockEditor';

export function SheetsTab(): JSX.Element {
  const { project, jobs } = useProjectOutlet();
  const { toast } = useToast();

  const [sheets, setSheets] = useState<Sheet[] | null>(null);
  const [summary, setSummary] = useState<SheetSetSummary | null>(null);
  const [tray, setTray] = useState<ReviewTrayData | null>(null);
  const [trayLoading, setTrayLoading] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [exporting, setExporting] = useState<ExportKind | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showTitleBlock, setShowTitleBlock] = useState(false);

  const sheetJobs = jobs.filter((job) => job.kind === 'sheets' || job.kind === 'export');
  const activeSheetJob = sheetJobs.find(
    (job) => job.kind === 'sheets' && (job.status === 'queued' || job.status === 'running'),
  );

  // -- loading ------------------------------------------------------------
  const reload = useCallback(async () => {
    try {
      const [set, sum] = await Promise.all([
        fetchSheetSet(project.id),
        fetchSheetSummary(project.id),
      ]);
      setSheets(set.sheets);
      setSummary(sum);
      setLoadError(null);
      setSelected((current) =>
        current && set.sheets.some((s) => s.id === current)
          ? current
          : (set.sheets[0]?.id ?? null),
      );
    } catch (cause: unknown) {
      setSheets([]);
      setLoadError(
        cause instanceof AppError
          ? `${cause.message} ${cause.action}`.trim()
          : 'We could not load your drawing set.',
      );
    }
  }, [project.id]);

  const reloadTray = useCallback(async () => {
    setTrayLoading(true);
    try {
      setTray(await fetchReviewTray(project.id));
    } catch {
      // A tray that cannot load must not take the sheets down with it; the card
      // shows its own empty state and the refresh button stays live.
      setTray(null);
    } finally {
      setTrayLoading(false);
    }
  }, [project.id]);

  useEffect(() => {
    void reload();
    void reloadTray();
  }, [reload, reloadTray]);

  // When a sheet job finishes, the set on screen is stale by definition. Watching the
  // job's terminal transition — not polling — keeps this honest and cheap.
  const lastJobState = useRef<string>('');
  useEffect(() => {
    const state = sheetJobs.map((job) => `${job.id}:${job.status}`).join('|');
    if (state === lastJobState.current) return;
    const finished = sheetJobs.some(
      (job) => job.kind === 'sheets' && job.status === 'succeeded',
    );
    lastJobState.current = state;
    if (finished) {
      void reload();
      void reloadTray();
    }
  }, [reload, reloadTray, sheetJobs]);

  // -- actions ------------------------------------------------------------
  const generate = useCallback(async () => {
    setGenerating(true);
    try {
      const result = await api.sheets.generate(project.id, {});
      if (result.job) {
        useJobsStore.getState().track(project.id, result.job as never);
        toast({
          severity: 'info',
          title: 'Drawing the set',
          description: 'Sheet generation started. It updates here as each sheet is drawn.',
        });
      }
      await reload();
    } catch (cause: unknown) {
      toast({
        severity: 'fail',
        title: "Couldn't start the drawing set",
        description:
          cause instanceof AppError ? `${cause.message} ${cause.action}`.trim() : 'Something went wrong.',
        action: { label: 'Try again', onClick: () => void generate() },
      });
    } finally {
      setGenerating(false);
    }
  }, [project.id, reload, toast]);

  const startExport = useCallback(
    async (kind: ExportKind) => {
      setExporting(kind);
      try {
        const job = await api.exports.create(project.id, { kind });
        useJobsStore.getState().track(project.id, job as never);
        toast({
          severity: 'info',
          title: 'Preparing your download',
          description: 'It appears in the jobs list below, then downloads on its own.',
        });
      } catch (cause: unknown) {
        toast({
          severity: 'fail',
          title: "Couldn't start that download",
          description:
            cause instanceof AppError
              ? `${cause.message} ${cause.action}`.trim()
              : 'Something went wrong.',
          action: { label: 'Try again', onClick: () => void startExport(kind) },
        });
      } finally {
        setExporting(null);
      }
    },
    [project.id, toast],
  );

  const downloadSheet = useCallback(
    async (sheetId: string, format: 'svg' | 'dxf' | 'pdf') => {
      try {
        const link = await sheetDownloadLink(project.id, sheetId, format);
        window.open(link.url, '_blank', 'noopener,noreferrer');
      } catch (cause: unknown) {
        toast({
          severity: 'fail',
          title: `That sheet has no ${format.toUpperCase()} yet`,
          description:
            cause instanceof AppError
              ? `${cause.message} ${cause.action}`.trim()
              : 'Generate the set again and try once more.',
          action: { label: 'Regenerate', onClick: () => void generate() },
        });
      }
    },
    [generate, project.id, toast],
  );

  const selectedSheet = useMemo(
    () => sheets?.find((sheet) => sheet.id === selected) ?? null,
    [selected, sheets],
  );

  // -- render -------------------------------------------------------------
  const hasSet = (sheets?.length ?? 0) > 0;

  return (
    <PageBody className="max-w-6xl">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-base font-semibold text-ink">Drawing set</h1>
          <p className="mt-0.5 max-w-2xl text-xs leading-5 text-ink-muted">
            The municipal submission set, dimensioned from the model. Vector PDF and layered
            DXF, print-true at 1:100.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => setShowTitleBlock((v) => !v)}>
            <Icon name="edit" size={13} /> Title block
          </Button>
          <Button
            onClick={() => void generate()}
            disabled={generating || Boolean(activeSheetJob)}
            data-testid="generate-sheets"
          >
            {activeSheetJob ? (
              <>
                <Spinner size={13} /> Drawing…
              </>
            ) : hasSet ? (
              'Regenerate set'
            ) : (
              'Generate the set'
            )}
          </Button>
        </div>
      </header>

      {showTitleBlock ? <TitleBlockEditor className="mt-4" /> : null}

      {/* ── set-level truth ────────────────────────────────────────────── */}
      {summary && summary.sheetCount > 0 ? (
        <div
          className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-line bg-surface px-4 py-2.5 text-2xs text-ink-muted"
          data-testid="sheet-set-summary"
        >
          <span className="font-medium text-ink">{summary.sheetCount} sheets</span>
          <span>
            {summary.chainCount} dimension chains ·{' '}
            {summary.chainSumOk ? (
              <span className="text-pass-ink" data-testid="chain-sum-ok">
                every chain sums exactly
              </span>
            ) : (
              <span className="font-semibold text-fail-ink" data-testid="chain-sum-bad">
                a chain does not add up — do not print this set
              </span>
            )}
          </span>
          {summary.labelCollisions > 0 ? (
            <span className="text-warn-ink">
              {summary.labelCollisions} overlapping label
              {summary.labelCollisions === 1 ? '' : 's'} — check before printing
            </span>
          ) : null}
          {summary.formatsAvailable.length > 0 ? (
            <span>available as {summary.formatsAvailable.join(', ').toUpperCase()}</span>
          ) : null}
        </div>
      ) : null}

      {/* Anything the worker could not draw, and why. Never silently missing. */}
      {summary && (summary.skipped.length > 0 || summary.notes.length > 0) ? (
        <ul className="mt-2 space-y-1" data-testid="sheet-set-notes">
          {summary.skipped.map((entry, index) => (
            <li
              key={`skip-${index}`}
              className="flex items-start gap-2 rounded-md bg-warn-soft px-3 py-2 text-2xs leading-4 text-warn-ink"
            >
              <Icon name="alert-triangle" size={13} className="mt-px shrink-0" />
              <span>
                <strong>{String(entry['number'] ?? entry['sheetId'] ?? 'A sheet')}</strong> was not
                drawn: {String(entry['reason'] ?? 'no reason given')}
              </span>
            </li>
          ))}
          {summary.notes.map((note, index) => (
            <li
              key={`note-${index}`}
              className="flex items-start gap-2 rounded-md bg-surface-muted px-3 py-2 text-2xs leading-4 text-ink-muted"
            >
              <Icon name="info" size={13} className="mt-px shrink-0" />
              <span>{note}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {loadError ? (
        <p className="mt-4 rounded-md border border-line bg-surface p-4 text-xs text-fail-ink">
          {loadError}
        </p>
      ) : null}

      {/* ── the set ─────────────────────────────────────────────────────── */}
      {sheets === null ? (
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }, (_, i) => (
            <div key={i} className="h-44 animate-pulse rounded-md border border-line bg-surface-muted" />
          ))}
        </div>
      ) : hasSet ? (
        <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,340px)_minmax(0,1fr)]">
          <div
            className="grid max-h-[32rem] gap-3 overflow-y-auto pr-1 sm:grid-cols-2 lg:grid-cols-1"
            data-testid="sheet-grid"
          >
            {sheets.map((sheet) => (
              <SheetThumbnail
                key={sheet.id}
                projectId={project.id}
                sheet={sheet}
                selected={sheet.id === selected}
                onSelect={setSelected}
              />
            ))}
          </div>

          <div className="flex min-h-[24rem] flex-col gap-2">
            {selectedSheet ? (
              <>
                <SheetViewer
                  projectId={project.id}
                  sheetId={selectedSheet.id}
                  label={`${selectedSheet.number ?? ''} ${selectedSheet.title ?? ''}`.trim()}
                  className="min-h-[24rem] flex-1"
                />
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-2xs text-ink-muted">Download this sheet:</span>
                  {(['pdf', 'dxf', 'svg'] as const).map((format) => {
                    const ready = Boolean(selectedSheet.artifacts[format]);
                    return (
                      <Button
                        key={format}
                        size="sm"
                        variant="ghost"
                        disabled={!ready}
                        title={
                          ready
                            ? `Download ${selectedSheet.number ?? 'this sheet'} as ${format.toUpperCase()}`
                            : `This server did not produce a ${format.toUpperCase()} for this sheet`
                        }
                        onClick={() => void downloadSheet(selectedSheet.id, format)}
                        data-testid={`sheet-download-${format}`}
                      >
                        <Icon name="download" size={13} /> {format.toUpperCase()}
                      </Button>
                    );
                  })}
                </div>
              </>
            ) : null}
          </div>
        </div>
      ) : (
        <SheetsEmptyState onGenerate={() => void generate()} busy={generating} />
      )}

      {/* ── whole-set downloads ─────────────────────────────────────────── */}
      {hasSet ? (
        <section className="mt-6" aria-label="Downloads">
          <h2 className="mb-2 text-sm font-semibold text-ink">Download the set</h2>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {EXPORT_OPTIONS.map((option) => (
              <button
                key={option.kind}
                type="button"
                disabled={exporting !== null}
                onClick={() => void startExport(option.kind)}
                data-testid={`export-${option.kind}`}
                className={cn(
                  'flex flex-col gap-1 rounded-md border border-line bg-surface p-3 text-left transition',
                  exporting === option.kind ? 'opacity-60' : 'hover:border-ink-muted',
                )}
              >
                <span className="flex items-center gap-2 text-xs font-medium text-ink">
                  <Icon name={option.icon} size={14} />
                  {option.label}
                  {exporting === option.kind ? <Spinner size={12} /> : null}
                </span>
                <span className="text-2xs leading-4 text-ink-muted">{option.detail}</span>
              </button>
            ))}
          </div>
          <p className="mt-2 text-2xs text-ink-muted">
            Each download is a background job; the link it produces expires in ten minutes, so
            start it when you are ready to save the file.
          </p>
        </section>
      ) : null}

      {/* ── review tray ─────────────────────────────────────────────────── */}
      <ReviewTray className="mt-6" tray={tray} loading={trayLoading} onRefresh={() => void reloadTray()} />

      {/* ── jobs ────────────────────────────────────────────────────────── */}
      <section className="mt-6" aria-label="Drawing jobs">
        <h2 className="mb-2 text-sm font-semibold text-ink">Jobs</h2>
        {sheetJobs.length === 0 ? (
          <EmptyState
            size="sm"
            icon="clock"
            title="Nothing running"
            description="Generating a set and every download run as background jobs. Their real progress appears here."
            demoAction={{
              notApplicable: 'Job history is per project; the demo offer belongs on the dashboard.',
            }}
          />
        ) : (
          <JobList jobs={sheetJobs} />
        )}
      </section>
    </PageBody>
  );
}

/**
 * The empty state teaches (golden rule 8): it names the six sheets the corporation
 * expects, so an architect can tell in ten seconds whether this matches their
 * submission list, and offers one button to get there.
 */
function SheetsEmptyState({
  onGenerate,
  busy,
}: {
  onGenerate: () => void;
  busy: boolean;
}): JSX.Element {
  return (
    <Card className="mt-4">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-ink">No drawings yet</h2>
          <p className="mt-0.5 text-xs text-ink-muted">
            One click turns the current design into the six sheets below. It takes under a
            minute for a G+1.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone="outline">A2 landscape · 1:100</Badge>
          <Button onClick={onGenerate} disabled={busy} data-testid="generate-sheets-empty">
            {busy ? 'Starting…' : 'Generate the set'}
          </Button>
        </div>
      </div>
      <ul className="divide-y divide-line">
        {SHEET_KIND_INFO.map((info, index) => (
          <li key={info.kind} className="flex gap-3 px-4 py-3">
            <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded bg-surface-muted font-mono text-2xs font-semibold text-ink-muted">
              {String(index + 1).padStart(2, '0')}
            </span>
            <div>
              <h3 className="text-sm font-medium text-ink">{info.label}</h3>
              <p className="mt-0.5 text-xs leading-5 text-ink-muted">{info.detail}</p>
            </div>
          </li>
        ))}
      </ul>
      <div className="flex items-start gap-2 border-t border-line bg-surface-muted px-4 py-3 text-2xs leading-4 text-ink-muted">
        <Icon name="info" size={13} className="mt-px shrink-0" />
        <span>
          Every dimension on a drawing is in millimetres regardless of what the units toggle
          shows — that is the drafting convention, and it keeps chains summing exactly. GFC
          depth (a second section, electrical and plumbing layouts, standard details) follows
          in v1.1.
        </span>
      </div>
    </Card>
  );
}

export default SheetsTab;
