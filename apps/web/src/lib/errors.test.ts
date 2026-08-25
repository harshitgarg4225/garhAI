/**
 * Golden rule 9 — "errors say what to do next" — is only true if it holds for
 * the responses nobody designed: a 502 HTML page from a proxy, a body with no
 * `action`, a 409 that forgot its `headIdx`. Those are the cases here.
 */

import { describe, expect, it } from 'vitest';

import {
  AppError,
  ERROR_CODES,
  OpConflictError,
  OpRejectionError,
  networkError,
  normaliseIssue,
  problemToAppError,
  timeoutError,
  toProblemDetail,
} from './errors';

const ctx = { endpoint: 'POST /projects/p1/ops' };

describe('problemToAppError', () => {
  it('reads a well-formed problem+json body', () => {
    const err = problemToAppError(
      403,
      {
        code: 'permission_denied',
        message: 'Only an admin can do that.',
        action: 'Ask a firm admin.',
        requestId: 'req_1',
        firmId: 'firm_1',
      },
      ctx,
    );
    expect(err).toBeInstanceOf(AppError);
    expect(err.code).toBe('permission_denied');
    expect(err.action).toBe('Ask a firm admin.');
    expect(err.requestId).toBe('req_1');
    // Non-canonical members survive as context.
    expect(err.data.firmId).toBe('firm_1');
    expect(err.data.code).toBeUndefined();
  });

  it('always produces an action, even from a body that is not problem+json', () => {
    for (const status of [400, 401, 403, 404, 409, 413, 429, 500, 502]) {
      const err = problemToAppError(status, { __nonJsonBody: '<html>502</html>' }, ctx);
      expect(err.action.length, `status ${status}`).toBeGreaterThan(0);
      expect(err.message.length).toBeGreaterThan(0);
    }
  });

  it('turns an op-sequence conflict into the rebase signal', () => {
    const err = problemToAppError(
      409,
      {
        code: 'op_sequence_conflict',
        message: 'This design moved on.',
        action: 'Rebase and re-send.',
        headIdx: 214,
        baseIdx: 210,
      },
      ctx,
    );
    expect(err).toBeInstanceOf(OpConflictError);
    expect((err as OpConflictError).headIdx).toBe(214);
    expect((err as OpConflictError).baseIdx).toBe(210);
    // A conflict is resolved by rebasing, never by blind retry.
    expect(err.retryable).toBe(false);
  });

  it('defaults a conflict with no headIdx to -1 rather than throwing', () => {
    const err = problemToAppError(
      409,
      { code: 'op_sequence_conflict', message: 'stale', action: 'rebase' },
      ctx,
    );
    expect(err).toBeInstanceOf(OpConflictError);
    expect((err as OpConflictError).headIdx).toBe(-1);
  });

  it('turns a 422 with issues into a rejection carrying the fix', () => {
    const err = problemToAppError(
      422,
      {
        code: 'op_rejected',
        message: 'That opening is wider than its wall.',
        action: 'Make the opening narrower.',
        opType: 'opening.add',
        issues: [
          {
            code: 'OPENING_WIDER_THAN_WALL',
            message: 'Door is 1200mm on a 900mm wall segment.',
            elementId: 'opening_01J0000000000000000000D1',
            fix: 'Reduce the door to 800mm.',
            opIndex: 0,
          },
        ],
      },
      ctx,
    );
    expect(err).toBeInstanceOf(OpRejectionError);
    const rejection = err as OpRejectionError;
    expect(rejection.issues).toHaveLength(1);
    expect(rejection.firstFix).toBe('Reduce the door to 800mm.');
    // The wire's single `elementId` is normalised onto the model core's array.
    expect(rejection.issues[0]?.elementIds).toEqual(['opening_01J0000000000000000000D1']);
    expect(rejection.issues[0]?.severity).toBe('error');
  });

  it('classifies auth failures and retryables', () => {
    const expired = problemToAppError(401, { code: 'token_expired' }, ctx);
    expect(expired.isAuthFailure).toBe(true);

    const limited = problemToAppError(429, { code: 'rate_limited' }, { ...ctx });
    expect(limited.retryable).toBe(true);

    const denied = problemToAppError(403, { code: 'permission_denied' }, ctx);
    expect(denied.isAuthFailure).toBe(false);
    expect(denied.retryable).toBe(false);
  });
});

describe('normaliseIssue', () => {
  it('rejects anything without a code and a message', () => {
    expect(normaliseIssue(null)).toBeNull();
    expect(normaliseIssue({ code: 'X' })).toBeNull();
    expect(normaliseIssue({ message: 'x' })).toBeNull();
  });

  it('keeps an unknown code rather than dropping the issue', () => {
    const issue = normaliseIssue({ code: 'SOME_FUTURE_CODE', message: 'From a newer server.' });
    expect(issue?.code).toBe('SOME_FUTURE_CODE');
  });
});

describe('synthetic failures', () => {
  it('describes offline honestly, and says edits are safe', () => {
    const err = networkError('GET /projects', new TypeError('Failed to fetch'));
    expect(err.isOffline).toBe(true);
    expect(err.retryable).toBe(true);
    expect(err.action).toMatch(/connection/i);
  });

  it('marks a timeout retryable without calling it offline', () => {
    const err = timeoutError('GET /projects', 20_000);
    expect(err.code).toBe(ERROR_CODES.timeout);
    expect(err.isOffline).toBe(false);
    expect(err.retryable).toBe(true);
  });

  it('coerces anything thrown into an AppError with a next step', () => {
    expect(AppError.from(new Error('boom')).action.length).toBeGreaterThan(0);
    expect(AppError.from('boom').code).toBe(ERROR_CODES.internal);
    expect(AppError.from(AppError.from('boom'))).toBeInstanceOf(AppError);
  });
});

describe('toProblemDetail', () => {
  it('flattens to plain data, keeping headIdx on a conflict', () => {
    const detail = toProblemDetail(
      new OpConflictError({
        code: 'ignored',
        message: 'stale',
        action: 'rebase',
        headIdx: 7,
      }),
    );
    expect(detail).toMatchObject({ code: ERROR_CODES.opSequenceConflict, headIdx: 7 });
    // Plain value, not an Error instance: rendering state must be data.
    expect(detail instanceof Error).toBe(false);
  });
});
