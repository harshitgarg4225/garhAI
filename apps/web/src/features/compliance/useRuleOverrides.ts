/**
 * useRuleOverrides — accept a failing rule with a reason, or revoke that.
 *
 * The write goes to the API (`POST/DELETE /compliance/overrides`), not to the
 * model store: the server stamps who/when, validates the rule id against the
 * packs the project loads, appends the `plot.set_reg_profile` op and writes
 * the audit row in one transaction. What this hook adds is the client's half
 * of the loop — after the server has appended the op, `pull()` the model store
 * so the op lands locally, `baseIdx` advances, and the strip re-checks. Until
 * that pull the row on screen still says what it said; the busy state covers
 * the gap so nobody presses Accept twice.
 */

import { useCallback, useState } from 'react';
import { useToast } from '@garh/ui';

import { api } from '../../lib/api';
import { AppError } from '../../lib/errors';
import { useModelStore } from '../../stores/model';

export interface RuleOverrides {
  /** Record `{reason}` for `ruleId`. Resolves true on success. */
  readonly accept: (ruleId: string, reason: string) => Promise<boolean>;
  /** Remove the acknowledgement. Resolves true on success. */
  readonly revoke: (ruleId: string) => Promise<boolean>;
  /** The rule a request is in flight for, or null. */
  readonly busyRuleId: string | null;
  /** The last failure, cleared by the next attempt. */
  readonly error: AppError | null;
}

export function useRuleOverrides(projectId: string): RuleOverrides {
  const { toast } = useToast();
  const [busyRuleId, setBusyRuleId] = useState<string | null>(null);
  const [error, setError] = useState<AppError | null>(null);

  const run = useCallback(
    async (ruleId: string, action: () => Promise<unknown>, done: string): Promise<boolean> => {
      setBusyRuleId(ruleId);
      setError(null);
      try {
        await action();
        // The server appended an op on our behalf; bring it down so the
        // document, the undo base and the live re-check all see it.
        await useModelStore.getState().pull();
        toast({ severity: 'pass', title: done, description: ruleId });
        return true;
      } catch (err: unknown) {
        const appError = AppError.from(err);
        setError(appError);
        toast({
          severity: 'fail',
          title: "Couldn't update the override",
          description: appError.message,
          action: { label: 'Try again', onClick: () => void action() },
        });
        return false;
      } finally {
        setBusyRuleId(null);
      }
    },
    [toast],
  );

  const accept = useCallback(
    (ruleId: string, reason: string) =>
      run(
        ruleId,
        () => api.compliance.override(projectId, { ruleId, reason }),
        'Accepted with reason — logged',
      ),
    [projectId, run],
  );

  const revoke = useCallback(
    (ruleId: string) =>
      run(ruleId, () => api.compliance.revokeOverride(projectId, ruleId), 'Override revoked'),
    [projectId, run],
  );

  return { accept, revoke, busyRuleId, error };
}

export default useRuleOverrides;
