/**
 * DxfImportDialog — the F1 "import a DXF boundary" flow (playbook Phase 2).
 *
 *   choose file → POST /projects/:id/import/dxf (202 + job)
 *                → poll GET /import-jobs/:id until terminal
 *                → layer picker over `result.layers` (closed rings only)
 *                → ONE `plot.set_boundary` op with `source: "dxf"`
 *
 * Lives in `pages/`, not `components/`, because it owns data: it calls the API
 * and dispatches through the model store, which the presentational folder
 * forbids by contract. The geometry never touches this file beyond being
 * previewed — the worker already normalised every candidate to a CCW integer-mm
 * ring with its bbox at the origin, which is exactly what `plot.set_boundary`
 * validates. No float is parsed, scaled or rounded here (locked decision).
 *
 * Honesty rules made UI:
 *   - every waiting state names what is happening (upload vs parse), driven by
 *     the job record, never a bare spinner (§15);
 *   - a failed job shows the WORKER's own error copy verbatim — it was written
 *     as "what happened + what to do next" (golden rule 9) — plus a retry;
 *   - an assumed unit mapping ($INSUNITS 0 → mm) renders as a warning chip,
 *     not silence (golden rule 4);
 *   - applying is one op group: one undo returns the previous boundary.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { formatPlotArea } from '@garh/model';
import { Button, Chip, Dialog, Icon, Skeleton, SkeletonRegion, cn } from '@garh/ui';

import { api } from '../../lib/api';
import { AppError } from '../../lib/errors';
import type { DxfImportResult, DxfPolyline } from '../../lib/schemas';
import { useUiStore } from '../../stores/ui';
import { usePlotActions, useUnitsDisplay } from '../../features/plot';

export interface DxfImportDialogProps {
  readonly open: boolean;
  readonly onOpenChange: (open: boolean) => void;
  readonly projectId: string;
}

// ---------------------------------------------------------------------------
// State machine
// ---------------------------------------------------------------------------

type Stage =
  | { readonly phase: 'pick' }
  | { readonly phase: 'working'; readonly filename: string; readonly label: string }
  | {
      readonly phase: 'failed';
      readonly filename: string;
      readonly message: string;
      readonly action: string | null;
      readonly requestId: string | null;
    }
  | {
      readonly phase: 'review';
      readonly filename: string;
      readonly result: DxfImportResult;
      readonly selectedKey: string | null;
    };

interface Candidate {
  readonly key: string;
  readonly layer: string;
  readonly polyline: DxfPolyline;
}

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled']);
const POLL_INTERVAL_MS = 900;
/** Parse budget is 10s in the worker; anything past this is genuinely stuck. */
const POLL_DEADLINE_MS = 90_000;

/** Human copy for the worker's `skipped` counters. Unknown keys pass through. */
const SKIPPED_LABELS: Readonly<Record<string, string>> = {
  openPolylines: 'open polylines (a boundary must be closed)',
  overVertexCap: 'polylines with too many vertices',
  degenerate: 'degenerate shapes',
  unsupported: 'unsupported entities',
  polylinesOverCap: 'polylines beyond the per-layer cap',
  layersOverCap: 'layers beyond the cap',
};

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function candidates(result: DxfImportResult): Candidate[] {
  const out: Candidate[] = [];
  for (const layer of result.layers) {
    layer.polylines.forEach((polyline, i) => {
      out.push({ key: `${layer.name}#${i}`, layer: layer.name, polyline });
    });
  }
  return out;
}

/** The largest ring is the plot boundary far more often than not — preselect it. */
function largestKey(list: readonly Candidate[]): string | null {
  let best: Candidate | null = null;
  for (const c of list) {
    if (best === null || c.polyline.closedArea > best.polyline.closedArea) best = c;
  }
  return best?.key ?? null;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function DxfImportDialog({ open, onOpenChange, projectId }: DxfImportDialogProps): JSX.Element {
  const display = useUnitsDisplay();
  const actions = usePlotActions();
  const pushToast = useUiStore((s) => s.pushToast);

  const [stage, setStage] = useState<Stage>({ phase: 'pick' });
  const fileRef = useRef<HTMLInputElement>(null);
  /** Bumped when the dialog closes or a new run starts; stale runs check it. */
  const runRef = useRef(0);
  /** The last file, kept so "Try again" re-uploads without re-picking. */
  const lastFileRef = useRef<File | null>(null);

  // Closing the dialog abandons any in-flight run and resets for next time.
  useEffect(() => {
    if (!open) {
      runRef.current += 1;
      setStage({ phase: 'pick' });
    }
  }, [open]);

  const run = useCallback(
    async (file: File): Promise<void> => {
      const runId = ++runRef.current;
      const alive = (): boolean => runRef.current === runId;
      lastFileRef.current = file;

      const fail = (message: string, action: string | null, requestId: string | null): void => {
        if (alive()) setStage({ phase: 'failed', filename: file.name, message, action, requestId });
      };

      setStage({ phase: 'working', filename: file.name, label: 'Uploading the drawing…' });
      try {
        const job = await api.imports.uploadDxf({ projectId, file, filename: file.name });
        if (!alive()) return;

        setStage({
          phase: 'working',
          filename: file.name,
          label: 'Reading the drawing for closed boundaries…',
        });

        let current = job;
        const deadline = Date.now() + POLL_DEADLINE_MS;
        while (!TERMINAL.has(current.status)) {
          if (Date.now() > deadline) {
            fail(
              'The import is taking much longer than it should.',
              'The workers may be busy or down. Try the upload again in a minute.',
              null,
            );
            return;
          }
          await sleep(POLL_INTERVAL_MS);
          if (!alive()) return;
          current = await api.imports.job(job.id);
          if (!alive()) return;
        }

        if (current.status !== 'succeeded') {
          // The worker's error copy already says what happened and what to do
          // next — pass it through rather than paraphrasing it worse.
          fail(current.error ?? 'The import did not finish.', null, null);
          return;
        }
        if (current.result === null) {
          fail(
            'The parsed result is no longer available.',
            'Results are kept for a limited time. Upload the file again.',
            null,
          );
          return;
        }

        const list = candidates(current.result);
        setStage({
          phase: 'review',
          filename: file.name,
          result: current.result,
          selectedKey: largestKey(list),
        });
      } catch (err) {
        const error = AppError.from(err);
        if (error.isAborted || !alive()) return;
        fail(error.message, error.action, error.requestId);
      }
    },
    [projectId],
  );

  const onFilePicked = (files: FileList | null): void => {
    const file = files?.[0];
    if (file !== undefined) void run(file);
    // Allow re-selecting the same file after a failure.
    if (fileRef.current !== null) fileRef.current.value = '';
  };

  const apply = (): void => {
    if (stage.phase !== 'review' || stage.selectedKey === null) return;
    const chosen = candidates(stage.result).find((c) => c.key === stage.selectedKey);
    if (chosen === undefined) return;

    const result = actions.setBoundary(chosen.polyline.points, {
      source: 'dxf',
      label: 'Plot boundary imported from DXF',
    });
    if (!result.ok) {
      pushToast({
        tone: 'error',
        title: "That boundary couldn't be applied.",
        description:
          result.issues[0]?.message ?? 'The ring did not pass the model validation. Try another layer.',
      });
      return;
    }
    pushToast({
      tone: 'success',
      title: `Boundary imported from ${stage.filename}.`,
      description: 'Corners can be dragged and edge lengths retyped like any drawn boundary.',
    });
    onOpenChange(false);
  };

  const reviewList = useMemo(
    () => (stage.phase === 'review' ? candidates(stage.result) : []),
    [stage],
  );

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Import a DXF boundary"
      description="Export the plot outline from AutoCAD or any CAD tool as DXF (R12 or newer); we read the closed polylines and you pick the one that is the boundary."
      size="lg"
      dismissOnBackdrop={stage.phase !== 'review'}
      footer={
        stage.phase === 'review' ? (
          <>
            <Button variant="ghost" size="sm" onClick={() => setStage({ phase: 'pick' })}>
              Choose another file
            </Button>
            <Button
              variant="primary"
              size="sm"
              iconLeft="check"
              disabled={stage.selectedKey === null}
              onClick={apply}
            >
              Use this boundary
            </Button>
          </>
        ) : undefined
      }
    >
      <input
        ref={fileRef}
        type="file"
        accept=".dxf,application/dxf,image/vnd.dxf"
        className="sr-only"
        aria-label="DXF file"
        onChange={(e) => onFilePicked(e.target.files)}
      />

      {stage.phase === 'pick' ? (
        <div className="flex flex-col items-start gap-2">
          <Button
            variant="secondary"
            size="md"
            iconLeft="folder"
            onClick={() => fileRef.current?.click()}
          >
            Choose a DXF file
          </Button>
          <p className="text-xs leading-5 text-ink-muted">
            Only the boundary is read — layers, blocks and dimensions in the file are listed but
            never imported as geometry. Coordinates land as exact integer millimetres; a drawing
            without units declared is assumed to be in mm, and we say so before you apply.
          </p>
        </div>
      ) : null}

      {stage.phase === 'working' ? (
        <SkeletonRegion label={stage.label} className="flex flex-col gap-3">
          <p className="flex items-center gap-2 text-sm text-ink" role="status">
            <Icon name="sheet" size={16} className="shrink-0 text-ink-subtle" />
            <span className="min-w-0 truncate">{stage.filename}</span>
            <span className="text-ink-muted">— {stage.label}</span>
          </p>
          <div className="flex gap-3">
            <Skeleton className="h-24 w-24" shape="block" />
            <div className="flex-1">
              <Skeleton className="h-4 w-2/5" />
              <Skeleton className="mt-2 h-4 w-3/5" />
              <Skeleton className="mt-2 h-4 w-1/3" />
            </div>
          </div>
        </SkeletonRegion>
      ) : null}

      {stage.phase === 'failed' ? (
        <div role="alert" className="flex flex-col gap-3">
          <div className="flex items-start gap-2.5">
            <Icon name="alert-circle" size={16} className="mt-0.5 shrink-0 text-fail-ink" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-ink">
                {stage.filename} couldn&rsquo;t be imported.
              </p>
              <p className="mt-0.5 text-xs leading-5 text-ink-muted">
                {stage.message}
                {stage.action === null ? '' : ` ${stage.action}`}
              </p>
              {stage.requestId === null ? null : (
                <p className="mt-1 font-mono text-2xs text-ink-subtle">Request {stage.requestId}</p>
              )}
            </div>
          </div>
          <div className="flex gap-2">
            {lastFileRef.current === null ? null : (
              <Button
                variant="secondary"
                size="sm"
                iconLeft="refresh"
                onClick={() => {
                  const file = lastFileRef.current;
                  if (file !== null) void run(file);
                }}
              >
                Try again
              </Button>
            )}
            <Button variant="ghost" size="sm" onClick={() => fileRef.current?.click()}>
              Choose a different file
            </Button>
          </div>
        </div>
      ) : null}

      {stage.phase === 'review' ? (
        <div className="flex flex-col gap-3">
          <UnitsNote result={stage.result} />

          {reviewList.length === 0 ? (
            <div className="rounded-md border border-warn-line bg-warn-soft px-3 py-2.5 text-xs leading-5 text-warn-ink">
              <p className="font-medium">No closed boundary found in {stage.filename}.</p>
              <p className="mt-0.5">
                {stage.result.layers.length > 0
                  ? `Layers seen: ${stage.result.layers.map((l) => l.name).join(', ')}. `
                  : ''}
                Close the plot outline into a single polyline in your CAD tool and export again.
              </p>
            </div>
          ) : (
            <fieldset>
              <legend className="text-2xs font-semibold uppercase tracking-wider text-ink-subtle">
                Closed boundaries found ({reviewList.length})
              </legend>
              <ul className="mt-1.5 grid gap-2 sm:grid-cols-2">
                {reviewList.map((c) => {
                  const selected = c.key === stage.selectedKey;
                  return (
                    <li key={c.key}>
                      <label
                        className={cn(
                          'garh-focus-ring-within flex cursor-pointer items-center gap-3 rounded-lg border p-2.5 transition-colors',
                          selected
                            ? 'border-brand bg-brand-soft'
                            : 'border-line bg-surface hover:border-ink-subtle',
                        )}
                      >
                        <input
                          type="radio"
                          name="dxf-candidate"
                          className="sr-only"
                          checked={selected}
                          onChange={() => setStage({ ...stage, selectedKey: c.key })}
                        />
                        <RingPreview polyline={c.polyline} selected={selected} />
                        <span className="min-w-0">
                          <span className="block truncate text-xs font-medium text-ink">
                            {c.layer}
                          </span>
                          <span className="block text-2xs text-ink-muted garh-nums">
                            {formatPlotArea(c.polyline.closedArea, display)}
                          </span>
                          <span className="block text-2xs text-ink-subtle garh-nums">
                            {c.polyline.points.length} corners
                          </span>
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            </fieldset>
          )}

          <SkippedNote skipped={stage.result.skipped} />
        </div>
      ) : null}
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Sub-views
// ---------------------------------------------------------------------------

/** Golden rule 4: an assumed unit mapping is a visible chip, never silence. */
function UnitsNote({ result }: { result: DxfImportResult }): JSX.Element | null {
  const units = result.units;
  if (units === null) return null;
  if (units.assumed) {
    return (
      <Chip severity="warn" size="sm" icon="alert-triangle">
        The file declares no units ($INSUNITS 0) — coordinates were read as millimetres. If the
        drawing was in feet or metres, fix the units in CAD and re-export.
      </Chip>
    );
  }
  return (
    <Chip severity="neutral" size="sm" className="garh-nums">
      1 drawing unit = {units.mmPerUnit} mm (from the file&rsquo;s $INSUNITS)
    </Chip>
  );
}

/** What the parser dropped, and why — counted, never fatal, never hidden. */
function SkippedNote({ skipped }: { skipped: Readonly<Record<string, number>> }): JSX.Element | null {
  const parts = Object.entries(skipped)
    .filter(([, count]) => count > 0)
    .map(([key, count]) => `${count} ${SKIPPED_LABELS[key] ?? key}`);
  if (parts.length === 0) return null;
  return (
    <p className="text-2xs leading-4 text-ink-subtle">
      Skipped while reading: {parts.join('; ')}. Skipped entities are never imported silently.
    </p>
  );
}

/**
 * A thumbnail of the ring. Display-only SVG: the mm coordinates are mapped
 * into a 56px box for the eye; the op will carry the exact integers.
 */
function RingPreview({
  polyline,
  selected,
}: {
  polyline: DxfPolyline;
  selected: boolean;
}): JSX.Element {
  const SIZE = 56;
  const PAD = 4;
  const pts = polyline.points;
  let maxX = 1;
  let maxY = 1;
  for (const p of pts) {
    if (p.x > maxX) maxX = p.x;
    if (p.y > maxY) maxY = p.y;
  }
  const scale = (SIZE - 2 * PAD) / Math.max(maxX, maxY);
  const ox = (SIZE - maxX * scale) / 2;
  const oy = (SIZE - maxY * scale) / 2;
  // Screen y grows downward; plot y grows upward — flip so the shape reads
  // the same way the editor will draw it.
  const d = pts
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${(ox + p.x * scale).toFixed(1)} ${(SIZE - oy - p.y * scale).toFixed(1)}`)
    .join(' ');
  // Tokens store raw RGB channels so Tailwind can add alpha; wrap in rgb()
  // when using one directly (see packages/ui/src/tokens.css).
  const fill = selected ? 'rgb(var(--garh-brand-soft))' : 'none';
  return (
    <svg
      width={SIZE}
      height={SIZE}
      viewBox={`0 0 ${SIZE} ${SIZE}`}
      aria-hidden="true"
      className="shrink-0 rounded-md bg-canvas"
    >
      <path
        d={`${d} Z`}
        fill={fill}
        stroke="currentColor"
        strokeWidth={1.5}
        className={selected ? 'text-brand' : 'text-ink-subtle'}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

export default DxfImportDialog;
