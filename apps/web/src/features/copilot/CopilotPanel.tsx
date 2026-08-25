/**
 * CopilotPanel — the docked chat rail (§10, §12).
 *
 * The integrator mounts this on the project shell's right side; the panel
 * itself owns nothing but presentation. All conversation state lives in
 * `useCopilotStore`, all design state in `useModelStore`, and the visibility
 * flag is the ui store's existing `copilotOpen`, so the top-bar toggle, the
 * `/` shortcut and this close button cannot disagree.
 *
 * Honesty rules this file implements:
 *   - "Thinking…" renders ONLY while `busy` is true — the flag is set and
 *     cleared around the actual request, never animated on a timer.
 *   - Every proposal renders as: the intent sentence → the DiffPreview
 *     (before/after mini-canvases + plain-language op list) → Apply / Reject.
 *   - `cannotDo` and `needsClarification` use the shared DiffPreview cards;
 *     clarifications get quick-reply chips mined from the question.
 *   - Errors are problem+json all the way down; the fail-closed 429 gets its
 *     own calm copy in `useCopilot.ts`.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';

import { Button, Icon, IconButton, cn } from '@garh/ui';

import { DiffPreview } from '../../components/DiffPreview';
import { useUiStore } from '../../stores/ui';

import { MiniDocPlan } from './MiniDocPlan';
import { registerCopilotInput } from './focus';
import { clarificationChips } from './plain';
import { issueSummary, selectBusy, selectHistory, selectTurns, useCopilotStore } from './useCopilot';
import type { CopilotTurn } from './types';

/** Example commands the empty state teaches with (§15 "empty states teach"). */
const EXAMPLES = [
  'widen the kitchen door to 900',
  'add a window to the master bedroom',
  'rename bedroom 2 to guest bedroom',
] as const;

export interface CopilotPanelProps {
  readonly className?: string | undefined;
}

export function CopilotPanel({ className }: CopilotPanelProps): JSX.Element | null {
  const open = useUiStore((s) => s.copilotOpen);
  const setPanel = useUiStore((s) => s.setPanel);

  if (!open) return null;

  return (
    <aside
      aria-label="Copilot"
      className={cn(
        'flex h-full w-80 shrink-0 flex-col border-l border-line bg-surface xl:w-96',
        className,
      )}
    >
      <header className="flex items-center gap-2 border-b border-line px-3 py-2.5">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand-soft text-brand-ink">
          <Icon name="sparkles" size={15} />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-ink">Copilot</h2>
          <p className="truncate text-2xs text-ink-subtle">
            Previews every change — nothing applies until you say so.
          </p>
        </div>
        <IconButton
          label="Close the copilot"
          icon="x"
          size="sm"
          variant="ghost"
          onClick={() => setPanel('copilot', false)}
        />
      </header>

      <TurnList />
      <CommandBox />
    </aside>
  );
}

// ---------------------------------------------------------------------------
// The conversation
// ---------------------------------------------------------------------------

function TurnList(): JSX.Element {
  const turns = useCopilotStore(selectTurns);
  const listRef = useRef<HTMLDivElement | null>(null);

  // Keep the newest turn in view. Length + last status covers both "a turn
  // arrived" and "a skeleton became a diff", without scrolling on hover state.
  const lastStatus = turns[turns.length - 1]?.status;
  useEffect(() => {
    const node = listRef.current;
    if (node !== null) node.scrollTop = node.scrollHeight;
  }, [turns.length, lastStatus]);

  return (
    <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto px-3 py-3" aria-live="polite">
      {turns.length === 0 ? <EmptyState /> : null}
      <ol className="flex flex-col gap-3">
        {turns.map((turn) => (
          <li key={turn.id} className="flex flex-col gap-2">
            <p className="ml-8 self-end rounded-lg rounded-br-sm bg-brand-soft px-3 py-1.5 text-sm leading-5 text-brand-ink">
              {turn.command}
            </p>
            <TurnBody turn={turn} />
          </li>
        ))}
      </ol>
    </div>
  );
}

function EmptyState(): JSX.Element {
  const send = useCopilotStore((s) => s.send);
  return (
    <div className="mb-3 rounded-lg border border-dashed border-line p-3">
      <p className="text-sm leading-6 text-ink-muted">
        Describe a change in plain words and I&rsquo;ll turn it into a previewable edit —
        you see before and after, then apply or reject. One undo reverses anything I do.
      </p>
      <p className="mt-2 text-2xs font-semibold uppercase tracking-wider text-ink-subtle">
        Try one
      </p>
      <div className="mt-1.5 flex flex-col items-start gap-1">
        {EXAMPLES.map((example) => (
          <button
            key={example}
            type="button"
            onClick={() => void send(example)}
            className="garh-focus-ring rounded-md bg-surface-muted px-2 py-1 text-left text-xs text-ink hover:bg-surface-sunken"
          >
            &ldquo;{example}&rdquo;
          </button>
        ))}
      </div>
    </div>
  );
}

function TurnBody({ turn }: { turn: CopilotTurn }): JSX.Element | null {
  const store = useCopilotStore();
  const requestCanvasFocus = useUiStore((s) => s.requestCanvasFocus);

  const onHighlight = useCallback(
    (elementIds: readonly string[]) => {
      if (elementIds.length > 0) requestCanvasFocus(elementIds, `copilot:${turn.id}`);
    },
    [requestCanvasFocus, turn.id],
  );

  switch (turn.status) {
    case 'thinking':
      return (
        <div className="flex flex-col gap-1.5">
          <DiffPreview
            diff={null}
            loading
            onApply={() => undefined}
            onReject={() => undefined}
          />
          <div className="flex items-center justify-between px-0.5">
            {/* Honest by wiring: this row exists only while the request is live. */}
            <span className="text-2xs text-ink-subtle">Thinking…</span>
            <Button size="sm" variant="ghost" onClick={() => store.cancel()}>
              Stop
            </Button>
          </div>
        </div>
      );

    case 'ready': {
      if (turn.diff === null || turn.beforeDoc === null || turn.afterDoc === null) return null;
      const { beforeDoc, afterDoc, storeyId } = turn;
      const highlightIds = turn.diff.ops.flatMap((op) => op.elementIds);
      return (
        <DiffPreview
          diff={turn.diff}
          onApply={() => store.apply(turn.id)}
          onReject={() => store.reject(turn.id)}
          onHighlight={onHighlight}
          renderBefore={() => (
            <MiniDocPlan
              doc={beforeDoc}
              frameWith={afterDoc}
              storeyId={storeyId}
              label="Plan before the change"
            />
          )}
          renderAfter={() => (
            <MiniDocPlan
              doc={afterDoc}
              frameWith={beforeDoc}
              storeyId={storeyId}
              highlightIds={highlightIds}
              label="Plan after the change"
            />
          )}
        />
      );
    }

    case 'applied':
      return (
        <StatusRow icon="check" tone="pass">
          Applied — one undo puts it back.
        </StatusRow>
      );

    case 'rejected':
      return (
        <StatusRow icon="x" tone="muted">
          Dismissed — nothing was changed.
        </StatusRow>
      );

    case 'cancelled':
      return (
        <StatusRow icon="minus" tone="muted">
          Stopped before an answer arrived.
        </StatusRow>
      );

    case 'cannot':
      return (
        <DiffPreview
          diff={null}
          cannotDo={turn.proposal?.cannotDo ?? "I can't do that one yet."}
          onApply={() => undefined}
          onReject={() => store.reject(turn.id)}
        />
      );

    case 'clarify': {
      const question = turn.proposal?.needsClarification ?? 'Could you say a little more?';
      return (
        <DiffPreview
          diff={null}
          needsClarification={question}
          clarificationChips={clarificationChips(question)}
          onClarify={(reply) => void store.clarify(turn.id, reply)}
          onApply={() => undefined}
          onReject={() => store.reject(turn.id)}
        />
      );
    }

    case 'error': {
      const detail = issueSummary(turn.issues);
      return (
        <div className="flex flex-col gap-1.5">
          <DiffPreview
            diff={null}
            problem={turn.problem ?? undefined}
            onApply={() => undefined}
            onReject={() => store.reject(turn.id)}
            onRetry={() => void store.retry(turn.id)}
          />
          {detail === null ? null : (
            <p className="px-0.5 text-2xs leading-4 text-ink-subtle">{detail}</p>
          )}
        </div>
      );
    }

    default:
      return null;
  }
}

function StatusRow({
  icon,
  tone,
  children,
}: {
  icon: 'check' | 'x' | 'minus';
  tone: 'pass' | 'muted';
  children: string;
}): JSX.Element {
  return (
    <p
      className={cn(
        'flex items-center gap-1.5 rounded-md px-2 py-1 text-xs',
        tone === 'pass' ? 'bg-pass-soft text-pass-ink' : 'bg-surface-muted text-ink-muted',
      )}
    >
      <Icon name={icon} size={12} />
      {children}
    </p>
  );
}

// ---------------------------------------------------------------------------
// The command box
// ---------------------------------------------------------------------------

function CommandBox(): JSX.Element {
  const busy = useCopilotStore(selectBusy);
  const history = useCopilotStore(selectHistory);
  const send = useCopilotStore((s) => s.send);
  const cancel = useCopilotStore((s) => s.cancel);

  const [value, setValue] = useState('');
  /** null = editing fresh text; N = showing history[N]. */
  const [histPos, setHistPos] = useState<number | null>(null);
  /** What was in the box before ↑ started walking history. */
  const draftRef = useRef('');
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  // The `/` shortcut's target — registered for the panel's whole life.
  useEffect(() => {
    registerCopilotInput(inputRef.current);
    return () => registerCopilotInput(null);
  }, []);

  const submit = useCallback(() => {
    const command = value.trim();
    if (command === '' || busy) return;
    void send(command);
    setValue('');
    setHistPos(null);
    draftRef.current = '';
  }, [busy, send, value]);

  const onKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
      const node = event.currentTarget;

      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        submit();
        return;
      }
      if (event.key === 'Escape') {
        if (busy) {
          event.preventDefault();
          cancel();
        }
        return;
      }

      // ↑/↓ walk sent commands, but only from the text's edges so the arrows
      // still move the caret inside a multi-line command.
      if (event.key === 'ArrowUp' && history.length > 0) {
        const atStart = node.selectionStart === 0 && node.selectionEnd === 0;
        if (!atStart && value !== '') return;
        event.preventDefault();
        const next = histPos === null ? history.length - 1 : Math.max(0, histPos - 1);
        if (histPos === null) draftRef.current = value;
        setHistPos(next);
        setValue(history[next] ?? '');
        return;
      }
      if (event.key === 'ArrowDown' && histPos !== null) {
        const atEnd = node.selectionStart === value.length && node.selectionEnd === value.length;
        if (!atEnd) return;
        event.preventDefault();
        if (histPos >= history.length - 1) {
          setHistPos(null);
          setValue(draftRef.current);
        } else {
          const next = histPos + 1;
          setHistPos(next);
          setValue(history[next] ?? '');
        }
      }
    },
    [busy, cancel, history, histPos, submit, value],
  );

  return (
    <form
      className="border-t border-line px-3 py-2.5"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <label htmlFor="copilot-command" className="sr-only">
        Tell the copilot what to change
      </label>
      <div className="flex items-end gap-2">
        <textarea
          id="copilot-command"
          ref={inputRef}
          value={value}
          rows={2}
          placeholder={'Ask for a change… e.g. "widen the kitchen door to 900"'}
          onChange={(event) => {
            setValue(event.target.value);
            setHistPos(null);
          }}
          onKeyDown={onKeyDown}
          className={cn(
            'garh-focus-ring min-h-[3.25rem] w-full resize-none rounded-md border border-line',
            'bg-surface px-2.5 py-1.5 text-sm leading-5 text-ink placeholder:text-ink-subtle',
          )}
        />
        <Button
          type="submit"
          variant="primary"
          size="sm"
          disabled={busy || value.trim() === ''}
          loading={busy}
          loadingLabel="Waiting for the copilot"
        >
          Send
        </Button>
      </div>
      <p className="mt-1.5 text-2xs text-ink-subtle">
        <kbd className="rounded border border-line px-1">/</kbd> focuses this box ·{' '}
        <kbd className="rounded border border-line px-1">Enter</kbd> sends ·{' '}
        <kbd className="rounded border border-line px-1">↑</kbd> recalls a command
      </p>
    </form>
  );
}
