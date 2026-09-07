/**
 * useLiveCompliance — "changing city preset re-validates live" (Phase 2 DoD).
 *
 * The wiring is deliberately indirect: nothing here watches for reg-profile
 * ops specifically. The hook re-checks whenever `baseIdx` advances — that is,
 * whenever the server has CONFIRMED any op group — because the compliance
 * endpoint evaluates the server's op log, not the browser's optimistic
 * document. Re-checking on local dispatch would race the flush and return a
 * report for the state *before* the edit; waiting for `baseIdx` means the
 * answer always describes what the user just did. With the op round-trip
 * budget at <100ms and the debounce at 450ms (§14 allows ≤500ms), the chip
 * strip still updates well inside a second of the preset changing.
 *
 * Honesty states, kept distinct on purpose (§15):
 *   `issues === null`  → nothing has been checked (no plot, or engine said
 *                        why not). The strip renders "nothing to check yet".
 *   `issues === []`    → checked, nothing to report. Never conflated with the
 *                        above.
 *   `checking`         → a re-check is in flight; the previous results stay on
 *                        screen rather than flashing to a skeleton.
 *   `error`            → the LAST re-check failed. The results on screen are
 *                        whatever the previous successful check said, and may
 *                        describe a state several edits old. The strip and the
 *                        tab say so and offer `recheck`; a green strip that is
 *                        silently stale is the one state this hook must never
 *                        present as current.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { toComplianceReport } from '../features/compliance/report';
import type { ComplianceReportVM } from '../features/compliance/report';
import { api } from '../lib/api';
import { AppError } from '../lib/errors';
import { useModelStore } from '../stores/model';
import type { ComplianceIssueVM } from '../components';

/** §14: "compliance run ≤500ms debounce". Under the cap, over the flush. */
const DEBOUNCE_MS = 450;

export interface LiveCompliance {
  /** Mapped results, or `null` when nothing has been evaluated yet. */
  readonly issues: readonly ComplianceIssueVM[] | null;
  /** The whole report (areas, warnings, pack versions…), or `null` as above. */
  readonly report: ComplianceReportVM | null;
  /** True while a (re-)check is in flight. */
  readonly checking: boolean;
  /** The last re-check failure, or null. Results shown are stale when set. */
  readonly error: AppError | null;
  /** Run the check again now — the retry behind the error state. */
  readonly recheck: () => void;
}

export function useLiveCompliance(projectId: string): LiveCompliance {
  const status = useModelStore((s) => s.status);
  const modelProjectId = useModelStore((s) => s.projectId);
  const baseIdx = useModelStore((s) => s.baseIdx);

  const [report, setReport] = useState<ComplianceReportVM | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<AppError | null>(null);
  // Bumped by `recheck`; a dependency of the effect so a retry re-runs it
  // without pretending the server confirmed anything.
  const [attempt, setAttempt] = useState(0);

  // Results belong to a project; switching projects must not show the old
  // project's chips for even one frame. `checking` resets too — an aborted
  // in-flight check for the OLD project must not leave the strip saying
  // "re-checking…" forever (its own run's `finally` is cancelled with it).
  const shownFor = useRef<string | null>(null);
  if (shownFor.current !== projectId) {
    shownFor.current = projectId;
    if (report !== null) setReport(null);
    if (checking) setChecking(false);
    if (error !== null) setError(null);
  }

  const ready = projectId !== '' && status === 'ready' && modelProjectId === projectId;

  useEffect(() => {
    if (!ready) return undefined;

    let cancelled = false;
    const controller = new AbortController();

    const timer = setTimeout(() => {
      setChecking(true);
      api.compliance
        .get(projectId, { signal: controller.signal })
        .then((wire) => {
          if (cancelled) return;
          setError(null);
          // `evaluated: false` is "nobody has run the rules", never a pass —
          // keep it `null` so the strip says "nothing to check yet".
          setReport(wire.evaluated ? toComplianceReport(wire) : null);
        })
        .catch((err: unknown) => {
          const appError = AppError.from(err);
          if (cancelled || appError.isAborted) return;
          // Keep the last known results on screen; a blank strip mid-edit
          // would read as "everything passed", which is worse than stale.
          // `error` is what tells the strip to say "last check failed".
          setError(appError);
        })
        .finally(() => {
          if (!cancelled) setChecking(false);
        });
    }, DEBOUNCE_MS);

    return () => {
      cancelled = true;
      controller.abort();
      clearTimeout(timer);
    };
    // `baseIdx` is the trigger: it advances exactly when the server confirms
    // ops, which is the earliest moment a re-check can see the change.
    // `attempt` is the manual retry.
  }, [projectId, ready, baseIdx, attempt]);

  const recheck = useCallback(() => setAttempt((n) => n + 1), []);

  return { issues: report === null ? null : report.issues, report, checking, error, recheck };
}

export default useLiveCompliance;
