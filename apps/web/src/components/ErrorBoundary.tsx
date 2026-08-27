/**
 * ErrorBoundary + the problem+json rendering that golden rule 9 demands.
 *
 * "No raw exceptions to the UI. Every user-facing error: what happened, why (if
 * known), one-click next action."
 *
 * Two halves:
 *  - `toProblem(unknown)` turns ANY thrown value into `{code, message, action}`.
 *    A `TypeError: undefined is not a function` becomes "Something went wrong on
 *    this screen" plus a Try-again button; the raw text goes to the console and
 *    the details disclosure, never into the headline.
 *  - `resolveRecovery(problem)` maps the API's error code to a real button.
 *    The API's `action` field is a sentence for humans; the button needs a
 *    behaviour, and only the client knows which behaviours exist.
 *
 * The boundary catches render-time errors. Errors from async calls do not reach
 * it — those are caught by the page and rendered with `<ProblemPanel>`, which
 * is the same component, so both paths look identical to the user.
 */

import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { Button, Card, Icon } from '@garh/ui';
import type { Problem } from './types';

// ---------------------------------------------------------------------------
// Normalising anything into a Problem
// ---------------------------------------------------------------------------

function isProblemShaped(value: unknown): value is Problem {
  if (typeof value !== 'object' || value === null) return false;
  const v = value as Record<string, unknown>;
  return typeof v.code === 'string' && typeof v.message === 'string';
}

/**
 * Never throws, never returns undefined. If we cannot recognise the failure we
 * say so plainly rather than printing a stack trace at the user.
 */
export function toProblem(error: unknown): Problem {
  if (isProblemShaped(error)) return error;

  // `ApiError` (lib/api.ts) is expected to carry `.problem`; we duck-type it so
  // this file does not depend on the api module.
  if (typeof error === 'object' && error !== null && 'problem' in error) {
    const inner = (error as { problem: unknown }).problem;
    if (isProblemShaped(inner)) return inner;
  }

  if (error instanceof Error) {
    // Offline is the single most common "error" and has a specific fix.
    if (error.name === 'TypeError' && /fetch|network/i.test(error.message)) {
      return {
        code: 'network_unreachable',
        message: "We couldn't reach Garh AI. Your internet connection may have dropped.",
        action: 'Check your connection and try again.',
      };
    }
    return {
      code: 'client_error',
      message: 'Something went wrong on this screen.',
      action: 'Try again. Your work is saved — nothing was lost.',
    };
  }

  return {
    code: 'unknown_error',
    message: 'Something went wrong.',
    action: 'Try again in a moment.',
  };
}

// ---------------------------------------------------------------------------
// Recovery: code -> a button that actually does something
// ---------------------------------------------------------------------------

export type RecoveryKind = 'retry' | 'reload' | 'home' | 'signin' | 'none';

export interface Recovery {
  label: string;
  kind: RecoveryKind;
}

/**
 * Known API error codes come from the repository layer's typed exceptions
 * (`op_sequence_conflict`, `tenant_context_required`, …) and the auth router.
 * Anything unknown falls back to "Try again", which is always safe.
 */
const RECOVERY_BY_CODE: Readonly<Record<string, Recovery>> = {
  op_sequence_conflict: { label: 'Reload the latest version', kind: 'reload' },
  tenant_context_required: { label: 'Sign in again', kind: 'signin' },
  unauthenticated: { label: 'Sign in again', kind: 'signin' },
  token_expired: { label: 'Sign in again', kind: 'signin' },
  permission_denied: { label: 'Back to your projects', kind: 'home' },
  cross_tenant_access: { label: 'Back to your projects', kind: 'home' },
  entity_not_found: { label: 'Back to your projects', kind: 'home' },
  invalid_cursor: { label: 'Start from the top', kind: 'reload' },
  rate_limited: { label: 'Try again', kind: 'retry' },
  network_unreachable: { label: 'Try again', kind: 'retry' },
  client_error: { label: 'Try again', kind: 'retry' },
  unknown_error: { label: 'Try again', kind: 'retry' },
};

export function resolveRecovery(problem: Problem): Recovery {
  return RECOVERY_BY_CODE[problem.code] ?? { label: 'Try again', kind: 'retry' };
}

// ---------------------------------------------------------------------------
// ProblemPanel — the one error surface
// ---------------------------------------------------------------------------

export interface ProblemPanelProps {
  problem: Problem;
  /** Runs for `kind: 'retry'`. Omit and the retry button is not shown. */
  onRetry?: (() => void) | undefined;
  /** Navigates for `kind: 'home' | 'signin'`. Pages pass react-router's navigate. */
  onNavigate?: ((to: '/' | '/login') => void) | undefined;
  /** Original error, shown behind a disclosure for bug reports. */
  detail?: string | undefined;
  /** Fills the parent instead of sitting in a card. */
  fullPage?: boolean | undefined;
}

export function ProblemPanel({
  problem,
  onRetry,
  onNavigate,
  detail,
  fullPage = false,
}: ProblemPanelProps): JSX.Element {
  const recovery = resolveRecovery(problem);

  const runRecovery = (): void => {
    switch (recovery.kind) {
      case 'retry':
        onRetry?.();
        return;
      case 'reload':
        if (typeof window !== 'undefined') window.location.reload();
        return;
      case 'home':
        if (onNavigate !== undefined) onNavigate('/');
        else if (typeof window !== 'undefined') window.location.assign('/');
        return;
      case 'signin':
        if (onNavigate !== undefined) onNavigate('/login');
        else if (typeof window !== 'undefined') window.location.assign('/login');
        return;
      case 'none':
        return;
    }
  };

  const canRecover =
    recovery.kind !== 'none' && (recovery.kind !== 'retry' || onRetry !== undefined);

  const body = (
    <div className="flex flex-col items-start gap-3 p-5 text-left">
      <span className="flex h-9 w-9 items-center justify-center rounded-full bg-fail-soft text-fail-ink">
        <Icon name="alert-circle" size={19} />
      </span>

      <div>
        <h2 className="text-base font-semibold text-ink">{problem.message}</h2>
        {problem.action === undefined ? null : (
          <p className="mt-1 text-sm leading-6 text-ink-muted">{problem.action}</p>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {canRecover ? (
          <Button variant="primary" onClick={runRecovery} iconLeft="refresh">
            {recovery.label}
          </Button>
        ) : null}
        {recovery.kind !== 'home' ? (
          <Button
            variant="ghost"
            iconLeft="home"
            onClick={() => {
              if (onNavigate !== undefined) onNavigate('/');
              else if (typeof window !== 'undefined') window.location.assign('/');
            }}
          >
            Back to your projects
          </Button>
        ) : null}
      </div>

      <details className="w-full">
        <summary className="garh-focus-ring cursor-pointer rounded-sm text-2xs text-ink-subtle">
          Technical details
        </summary>
        <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded border border-line bg-surface-muted p-2 font-mono text-2xs text-ink-muted">
          {problem.code}
          {problem.status === undefined ? '' : ` · HTTP ${problem.status}`}
          {detail === undefined ? '' : `\n${detail}`}
        </pre>
      </details>
    </div>
  );

  if (fullPage) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center p-6">
        <Card className="w-full max-w-md">{body}</Card>
      </div>
    );
  }
  return <Card className="w-full">{body}</Card>;
}

// ---------------------------------------------------------------------------
// The boundary itself
// ---------------------------------------------------------------------------

export interface ErrorBoundaryProps {
  children: ReactNode;
  /** Names the region in the console log: "project shell", "renders tab". */
  region?: string | undefined;
  /**
   * Reported to the error hook (§18 "Sentry-compatible error hook"). The web
   * shell wires this to its reporter; the boundary itself has no dependency on
   * one.
   */
  onError?: ((error: unknown, info: { componentStack: string }) => void) | undefined;
  /** Replaces the default panel entirely. */
  fallback?: ((problem: Problem, reset: () => void) => ReactNode) | undefined;
  /** Changing any value here resets the boundary — pass the route key. */
  resetKey?: unknown;
}

interface ErrorBoundaryState {
  problem: Problem | null;
  detail: string | undefined;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  override state: ErrorBoundaryState = { problem: null, detail: undefined };

  static getDerivedStateFromError(error: unknown): Partial<ErrorBoundaryState> {
    return {
      problem: toProblem(error),
      detail: error instanceof Error ? `${error.name}: ${error.message}` : String(error),
    };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    // Always log the real thing for developers; never show it to the architect.

    console.error(`[garh] error in ${this.props.region ?? 'app'}`, error, info.componentStack);
    this.props.onError?.(error, { componentStack: info.componentStack ?? '' });
  }

  override componentDidUpdate(prev: ErrorBoundaryProps): void {
    if (prev.resetKey !== this.props.resetKey && this.state.problem !== null) {
      this.reset();
    }
  }

  reset = (): void => {
    this.setState({ problem: null, detail: undefined });
  };

  override render(): ReactNode {
    const { problem, detail } = this.state;
    if (problem === null) return this.props.children;
    if (this.props.fallback !== undefined) return this.props.fallback(problem, this.reset);
    return <ProblemPanel problem={problem} onRetry={this.reset} detail={detail} fullPage />;
  }
}
