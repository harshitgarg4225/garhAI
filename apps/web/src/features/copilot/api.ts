/**
 * The copilot endpoint — the ONE network call this feature makes.
 *
 * PHASE 6 INTEGRATION — what changed and why
 * ------------------------------------------
 * This file used to speak a contract the route does not implement, and both
 * halves were wrong in a way that would have failed on the very first command:
 *
 *   · **Request.** It sent `{command}`. `CopilotCommandIn` declares the field
 *     as `text` with `extra="forbid"`, so every send was a 422.
 *   · **Response.** It expected `{applicable, plainLanguage[]}` from an older
 *     `services/llm` shape. The route returns `CopilotProposeOut`:
 *     `{outcome, ops[{type,payload,description}], groupId, baseIdx, …}` — so
 *     even a well-formed answer would have parsed as `malformed_response`.
 *
 * The call itself now goes through `lib/api.ts::api.copilot.propose` (the
 * catalogued §11 surface, zod-validated there against the real server models),
 * and this module keeps its one job: adapting that wire shape to the
 * {@link CopilotProposal} view model the rest of the feature already reads.
 * `proposeCopilot`'s signature is unchanged, so `useCopilot.ts` did not move.
 *
 * What the client does NOT send: the model document. The server builds its own
 * model summary (rooms, storeys, key dims) from its own state, which is where
 * the §13 PII-exclusion rule is enforced — a client that shipped the doc up
 * would bypass it.
 */

import { api, type ApiClient } from '../../lib/api';
import { normaliseIssue, type ApiValidationIssue } from '../../lib/errors';
import type { CopilotPropose } from '../../lib/schemas';

import type { CopilotProposal, CopilotWireOp } from './types';

/**
 * `CopilotProposeOut` → the view model.
 *
 * `applicable` is DERIVED, not trusted from a flag: the server's four outcome
 * classes are the authority, and only `ops` (with at least one op) may be
 * previewed. `needsClarification` and `cannotDo` are forced to null on that
 * branch and vice versa, so the store's `settle()` can never see a refusal that
 * also carries ops — the "ops riding a refusal" case the API's own corpus tests
 * pin server-side, re-asserted here because this is the last gate before the
 * ops reach a dispatch path.
 */
export function toProposal(raw: CopilotPropose): CopilotProposal {
  const applicable = raw.outcome === 'ops' && raw.ops.length > 0;

  const ops: CopilotWireOp[] = applicable
    ? raw.ops.map((op) => ({ type: op.type, payload: op.payload }))
    : [];

  const issues = raw.issues
    .map((issue) => normaliseIssue(issue))
    .filter((issue): issue is ApiValidationIssue => issue !== null);

  return {
    applicable,
    intent: raw.intent,
    ops,
    // Index-aligned with `ops` by construction: same array, same order.
    plainLanguage: applicable ? raw.ops.map((op) => op.description) : [],
    cannotDo: applicable ? null : (raw.cannotDo ?? null),
    needsClarification: applicable ? null : (raw.needsClarification ?? null),
    issues,
    selfCorrected: raw.selfCorrected,
    // Server-minted. Applying with THIS group id is what lets the §10 eval log
    // correlate the proposal line with the decision line.
    groupId: raw.groupId,
    baseIdx: raw.baseIdx,
    attempts: raw.attempts,
    rulesChecked: raw.rulesChecked,
    dryRunMs: raw.dryRunMs,
  };
}

// ---------------------------------------------------------------------------
// The call
// ---------------------------------------------------------------------------

export interface ProposeInput {
  readonly projectId: string;
  readonly command: string;
  /** Context for element resolution ("this wall", "here"). Optional, never PII. */
  readonly activeStoreyId?: string | null | undefined;
  readonly selectionIds?: readonly string[] | undefined;
  readonly signal?: AbortSignal | undefined;
}

/**
 * Ask the copilot for a proposal. Throws `AppError` like every API call —
 * including the fail-closed 429 from the shared `llm.per_firm` limiter, which
 * `useCopilot.ts` renders with its own calm copy.
 */
export function proposeCopilot(
  input: ProposeInput,
  client: ApiClient = api,
): Promise<CopilotProposal> {
  return client.copilot
    .propose(
      input.projectId,
      {
        command: input.command,
        activeStoreyId: input.activeStoreyId ?? null,
        ...(input.selectionIds === undefined ? {} : { selectionIds: input.selectionIds }),
      },
      input.signal === undefined ? {} : { signal: input.signal },
    )
    .then(toProposal);
}
