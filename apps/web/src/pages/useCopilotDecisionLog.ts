/**
 * useCopilotDecisionLog — closes §10's eval loop: "Log {command, ops,
 * applied|rejected|invalid} for the eval set."
 *
 * The API writes the *proposal* half of that line itself, inside
 * `POST /projects/:id/copilot`. It cannot write the other half, because only
 * this browser knows what the human did with the diff — Apply dispatches
 * through the ordinary op sequencer (which sees `source: 'copilot'` but not
 * "this was proposal #7"), and Reject sends nothing at all, on purpose.
 * `POST /projects/:id/copilot/decision` exists for exactly this, and it is
 * log-only: no ops, no credits, no state.
 *
 * ## Why the hook lives here and not in the copilot store
 *
 * The store's contract is that it owns NO write path except
 * `useModelStore.dispatch` (golden rule 1, and the reason its tests can assert
 * "reject dispatches nothing" against a single mocked module). Teaching it to
 * POST on every settled turn would put a second network call inside that
 * guarantee. Instead the shell observes the turn list — the same public state
 * the panel renders — and reports transitions. If this hook never mounts, the
 * copilot works identically and only the eval corpus is poorer.
 *
 * ## Honesty rules
 *
 *  - Fire-and-forget. A failed log must never look like a failed apply, so
 *    errors are swallowed (they are already visible in the network panel).
 *  - Each turn is reported at most once, tracked by turn id.
 *  - The command text goes up as typed; the SERVER masks obvious identifiers
 *    with `strip_pii` before it reaches a log line (§13). We do not pre-mangle
 *    it here — two redaction implementations would drift, and the server's is
 *    the one with the tests.
 */

import { useEffect } from 'react';

import { useCopilotStore, type CopilotTurn } from '../features/copilot';
import { api } from '../lib/api';

/** Terminal human verdicts. Everything else is still in flight or was never a diff. */
const REPORTABLE = new Set<CopilotTurn['status']>(['applied', 'rejected']);

export function useCopilotDecisionLog(projectId: string): void {
  useEffect(() => {
    if (projectId === '') return;
    const reported = new Set<string>();

    // Anything already terminal when the shell mounts was decided in a
    // previous mount of this same tab and has been reported already; seed the
    // set so a tab switch does not double-log it.
    for (const turn of useCopilotStore.getState().turns) {
      if (REPORTABLE.has(turn.status)) reported.add(turn.id);
    }

    return useCopilotStore.subscribe((state) => {
      for (const turn of state.turns) {
        if (!REPORTABLE.has(turn.status)) continue;
        if (reported.has(turn.id)) continue;
        reported.add(turn.id);

        void api.copilot
          .decision(projectId, {
            command: turn.command,
            outcome: turn.status === 'applied' ? 'applied' : 'rejected',
            // On reject the store clears `ops`, so the count comes from the
            // proposal — what was OFFERED is what the eval set wants to know.
            opsCount: turn.proposal?.ops.length ?? turn.ops.length,
            groupId: turn.groupId,
            intent: turn.proposal?.intent ?? null,
          })
          .catch(() => {
            /* Logging is best-effort by design — see the header. */
          });
      }
    });
  }, [projectId]);
}
