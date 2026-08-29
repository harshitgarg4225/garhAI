/**
 * ComparePanel.tsx — choosing two versions and reading what differs (C-8).
 *
 * The op log has always supported branching; nothing surfaced it. An architect holding
 * Option A and Option B wants one answer — what is actually different — and wants it in
 * elements, not in ops.
 *
 * ## Three things this panel refuses to do
 *
 * 1. **It does not default the second version to "latest".** A compare whose meaning
 *    changes when a collaborator saves is a compare nobody can cite in a client meeting.
 * 2. **It does not render "no changes" bare.** "No change" and "no change in the things
 *    I compared" are different claims, and the honest one names what was left out. Two
 *    plans that differ only in a slab would otherwise read as identical.
 * 3. **It does not hide what it could not draw.** A moved furniture item has no
 *    footprint in the model — the catalogue holds that — so it cannot get a box on the
 *    plan. It is listed anyway, marked as not shown, because a diff that quietly drops a
 *    change is the gate that never fires.
 */

import { useCallback, useEffect, useState } from 'react';

import { Badge, Button, Icon, cn } from '@garh/ui';

import { api } from '../../lib/api';
import { AppError } from '../../lib/errors';
import type { Version } from '../../lib/schemas';
import { loadCompare } from './api';
import { useCompareStore } from './store';

export interface ComparePanelProps {
  projectId: string;
  className?: string;
}

/** Square metres, to one decimal — the unit an Indian architect quotes areas in. */
function sqm(mm2: number): string {
  return `${(mm2 / 1_000_000).toFixed(1)} m²`;
}

function delta(before: number, after: number): string {
  const diff = after - before;
  if (diff === 0) return 'no change';
  return `${diff > 0 ? '+' : '−'}${sqm(Math.abs(diff))}`;
}

export function ComparePanel({ projectId, className }: ComparePanelProps): JSX.Element {
  const [versions, setVersions] = useState<Version[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const { a, b, result, loading, error, overlayVisible, setA, setB, toggleOverlay } =
    useCompareStore();

  useEffect(() => {
    const controller = new AbortController();
    api.versions
      .list(projectId, { limit: 50, signal: controller.signal })
      .then((page) => {
        if (!controller.signal.aborted) setVersions(page.items);
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setListError(
          cause instanceof AppError ? cause.message : 'Could not load the version list.',
        );
      });
    return () => controller.abort();
  }, [projectId]);

  const compare = useCallback(() => {
    void loadCompare(projectId);
  }, [projectId]);

  const label = (version: Version): string =>
    version.name ?? `${version.kind} · ${new Date(version.createdAt).toLocaleDateString('en-IN')}`;

  return (
    <section
      className={cn('rounded-lg border border-line p-4', className)}
      aria-label="Compare versions"
      data-testid="compare-panel"
    >
      <h3 className="text-sm font-medium text-ink">Compare versions</h3>

      {listError ? <p className="mt-2 text-sm text-fail">{listError}</p> : null}

      {versions !== null && versions.length < 2 ? (
        <p className="mt-2 text-sm text-ink-muted">
          Save a second version and you can compare them side by side.
        </p>
      ) : null}

      {versions !== null && versions.length >= 2 ? (
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          <label className="text-2xs text-ink-muted">
            From
            <select
              className="mt-1 w-full rounded border border-line bg-surface px-2 py-1 text-sm"
              value={a ?? ''}
              aria-label="Compare from"
              onChange={(event) => setA(event.currentTarget.value || null)}
            >
              <option value="">Choose a version…</option>
              {versions.map((version) => (
                <option key={version.id} value={version.id}>
                  {label(version)}
                </option>
              ))}
            </select>
          </label>
          <label className="text-2xs text-ink-muted">
            To
            <select
              className="mt-1 w-full rounded border border-line bg-surface px-2 py-1 text-sm"
              value={b ?? ''}
              aria-label="Compare to"
              onChange={(event) => setB(event.currentTarget.value || null)}
            >
              <option value="">Choose a version…</option>
              {versions.map((version) => (
                <option key={version.id} value={version.id}>
                  {label(version)}
                </option>
              ))}
            </select>
          </label>
        </div>
      ) : null}

      {versions !== null && versions.length >= 2 ? (
        <div className="mt-3 flex items-center gap-2">
          <Button size="sm" disabled={a === null || b === null || loading} onClick={compare}>
            {loading ? 'Comparing…' : 'Compare'}
          </Button>
          {result ? (
            <Button size="sm" variant="ghost" onClick={toggleOverlay}>
              {overlayVisible ? 'Hide on plan' : 'Show on plan'}
            </Button>
          ) : null}
        </div>
      ) : null}

      {error ? <p className="mt-3 text-sm text-fail">{error}</p> : null}

      {result ? (
        <div className="mt-4 border-t border-line pt-3" data-testid="compare-result">
          <p className="text-sm text-ink">{result.summary}</p>

          {/* Stated whether or not anything changed. Without it, "no geometric change"
              is read as "these are identical", which is a different claim. */}
          <p className="mt-1 text-2xs text-ink-subtle">
            Compared: {result.comparedKinds.join(', ')}.
            {Object.entries(result.excludedKinds).map(([kind, reason]) => (
              <span key={kind}>
                {' '}
                {kind} is not compared — {reason}.
              </span>
            ))}
          </p>

          {result.areasA && result.areasB ? (
            <p className="mt-2 text-sm text-ink-muted" data-testid="compare-areas">
              Built-up {sqm(result.areasA.builtUpMm2)} → {sqm(result.areasB.builtUpMm2)} (
              {delta(result.areasA.builtUpMm2, result.areasB.builtUpMm2)})
              {result.areasA.farAchieved !== null && result.areasB.farAchieved !== null ? (
                <>
                  {' · FAR '}
                  {result.areasA.farAchieved.toFixed(2)} → {result.areasB.farAchieved.toFixed(2)}
                </>
              ) : null}
            </p>
          ) : null}

          {result.changes.length > 0 ? (
            <ul className="mt-3 max-h-56 space-y-1 overflow-y-auto text-sm text-ink-muted">
              {result.changes.map((change) => (
                <li key={`${change.kind}:${change.elementId}`} className="flex gap-2">
                  <Badge tone={change.change === 'removed' ? 'fail' : 'info'}>
                    {change.change}
                  </Badge>
                  <span>
                    {change.kind}
                    {change.fields.length > 0 ? ` — ${change.fields.join(', ')}` : ''}
                    {change.derived ? ' (follows another change)' : ''}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}

          {result.unplaced.length > 0 ? (
            <p className="mt-3 text-2xs text-ink-subtle" data-testid="compare-unplaced">
              <Icon name="info" className="mr-1 inline h-3 w-3" aria-hidden />
              {result.unplaced.length} change
              {result.unplaced.length === 1 ? '' : 's'} cannot be shown on the plan (
              {[...new Set(result.unplaced.map((item) => item.kind))].join(', ')}) — they are
              counted above.
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

export default ComparePanel;
