/**
 * FreeTextParse — paste (or dictate) the client's words, get a structured
 * brief back, review every assumption, then apply — or don't.
 *
 * The §10 / golden-rule-4 contract, made UI:
 *
 *   text ──POST /projects/:id/brief/parse──▶ { data, assumptions[], … }
 *
 *   1. NOTHING applies silently. The parse is requested with `apply: false`
 *      and held locally as a pending review. The brief in the model store does
 *      not move until the architect presses "Use this brief".
 *   2. EVERY assumption is an editable chip (field · value · reason). Editing
 *      a chip rewrites the pending data via `setBriefField` — dotted paths,
 *      exactly as the parser emits them — and marks the chip as yours.
 *   3. Apply is ONE `brief.update` op: the pending data (minus keys the brief
 *      already holds, minus `vastuMode`, which travels on the op's own field)
 *      as a single merge patch → one group → ONE undo step, offered in the
 *      confirmation toast.
 *
 * States are explicit and all four are designed: idle (teaches), parsing
 * (skeleton chips, honest label), error (what happened + retry), review.
 */

import { useCallback, useMemo, useState } from 'react';

import {
  VASTU_MODES,
  formatIndianNumber,
  formatRupeesCompact,
  formatSqft,
  parseAreaMm2,
  type JsonObject,
  type JsonValue,
  type VastuMode,
} from '@garh/model';

import {
  AssumptionChip,
  Button,
  Card,
  CardHeader,
  Chip,
  Icon,
  Skeleton,
  SkeletonRegion,
  Textarea,
  cn,
} from '@garh/ui';

import { api } from '../../lib/api';
import { AppError } from '../../lib/errors';
import type { BriefAssumption } from '../../lib/schemas';
import { useModelStore } from '../../stores/model';
import { useUiStore } from '../../stores/ui';
import { canonicaliseParsedData, pruneUnchanged, setBriefField } from './mergePatch';
import { bedroomRows, otherRooms, parseRupees, readBriefData, roomTypeLabel } from './types';
import { useBrief } from './useBrief';

export interface FreeTextParseProps {
  readonly projectId: string;
  readonly className?: string | undefined;
}

// ---------------------------------------------------------------------------
// Pending-review state machine
// ---------------------------------------------------------------------------

interface ReviewState {
  readonly phase: 'review';
  readonly text: string;
  /** The parse result, canonicalised, WITH the user's chip edits applied. */
  readonly data: JsonObject;
  readonly assumptions: readonly BriefAssumption[];
  /** Assumption fields the user has edited (their value now, not ours). */
  readonly edited: ReadonlySet<string>;
  readonly warnings: readonly string[];
}

type ParseUiState =
  | { readonly phase: 'idle' }
  | { readonly phase: 'parsing'; readonly text: string }
  | { readonly phase: 'error'; readonly text: string; readonly error: AppError }
  | ReviewState;

// ---------------------------------------------------------------------------
// Field-aware formatting / parsing for assumption chips
// ---------------------------------------------------------------------------

const FIELD_LABELS: Readonly<Record<string, string>> = {
  storeys: 'Floors',
  hasStilt: 'Stilt',
  hasBasement: 'Basement',
  terraceAccess: 'Terrace access',
  futureExpansion: 'Future expansion',
  budgetInr: 'Budget',
  ratePerSqftInr: 'Rate',
  parkingCount: 'Parking',
  familySize: 'Family size',
  vastuMode: 'Vastu',
  kitchenType: 'Kitchen',
  livingDining: 'Living/dining',
};

/** 'brief.rooms.bath_wc.count' → ['rooms','bath_wc','count']; 'brief.storeys' → ['storeys']. */
function fieldParts(field: string): string[] {
  return (field.startsWith('brief.') ? field.slice('brief.'.length) : field).split('.');
}

function fieldLabel(field: string): string {
  const parts = fieldParts(field);
  if (parts.length === 3 && parts[0] === 'rooms' && parts[2] === 'count') {
    return `${roomTypeLabel(parts[1] ?? '')} count`;
  }
  const key = parts[0] ?? field;
  const known = FIELD_LABELS[key];
  if (known !== undefined) return known;
  return key.replace(/([A-Z])/g, ' $1').replace(/^\w/, (c) => c.toUpperCase());
}

function formatFieldValue(field: string, value: unknown): string {
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (value === null || value === undefined) return '—';
  const key = fieldParts(field)[0] ?? '';
  if (typeof value === 'number') {
    if (key === 'budgetInr') return formatRupeesCompact(value);
    if (key === 'ratePerSqftInr') return `₹${formatIndianNumber(value)} / sq ft`;
    if (field.endsWith('targetAreaMm2')) return formatSqft(value, 0);
    if (key === 'storeys') return value <= 1 ? 'Ground only' : `G+${value - 1}`;
    return formatIndianNumber(value);
  }
  return String(value);
}

const TRUE_WORDS = new Set(['yes', 'y', 'true', 'on', 'haan', '1']);
const FALSE_WORDS = new Set(['no', 'n', 'false', 'off', 'nahi', '0']);

/**
 * Parse a chip edit back into the field's JSON type. Returns `undefined` when
 * the text cannot be read — the caller says so instead of guessing.
 */
function parseFieldValue(field: string, previous: unknown, raw: string): JsonValue | undefined {
  const s = raw.trim();
  if (s === '') return undefined;
  const key = fieldParts(field)[0] ?? '';
  const lower = s.toLowerCase();

  if (typeof previous === 'boolean') {
    if (TRUE_WORDS.has(lower)) return true;
    if (FALSE_WORDS.has(lower)) return false;
    return undefined;
  }
  if (key === 'vastuMode') {
    return (VASTU_MODES as readonly string[]).includes(lower) ? lower : undefined;
  }
  if (key === 'budgetInr' || key === 'ratePerSqftInr') {
    const rupees = parseRupees(s);
    return rupees !== null && rupees > 0 ? rupees : undefined;
  }
  if (field.endsWith('targetAreaMm2')) {
    try {
      const mm2 = parseAreaMm2(s, 'sqft');
      return mm2 > 0 ? mm2 : undefined;
    } catch {
      return undefined;
    }
  }
  if (key === 'storeys') {
    const g = /^g\s*\+\s*([0-9]+)$/.exec(lower);
    if (g !== null) return Number(g[1]) + 1;
    if (lower === 'g' || lower === 'ground') return 1;
  }
  if (typeof previous === 'number' || previous === undefined || previous === null) {
    const n = Number(s.replace(/,/g, ''));
    if (Number.isSafeInteger(n) && n >= 0) return n;
    if (typeof previous !== 'number') return s; // free-text field stays text
    return undefined;
  }
  return s;
}

/** Read the value a dotted field currently has inside pending data. */
function getFieldValue(data: JsonObject, field: string): JsonValue | undefined {
  const parts = fieldParts(field);
  if (parts.length === 3 && parts[0] === 'rooms' && parts[2] === 'count') {
    const rooms = readBriefData(data).rooms ?? [];
    let total = 0;
    for (const room of rooms) if (room.type === parts[1]) total += room.count;
    return total;
  }
  const key = parts[0];
  return key === undefined ? undefined : data[key];
}

// ---------------------------------------------------------------------------
// The component
// ---------------------------------------------------------------------------

export function FreeTextParse({ projectId, className }: FreeTextParseProps): JSX.Element {
  const { rawData, ready, update } = useBrief();
  const pushToast = useUiStore((s) => s.pushToast);
  const [text, setText] = useState('');
  const [state, setState] = useState<ParseUiState>({ phase: 'idle' });

  const parse = useCallback(
    async (input: string): Promise<void> => {
      const trimmed = input.trim();
      if (trimmed === '') return;
      setState({ phase: 'parsing', text: trimmed });
      try {
        const result = await api.brief.parse(projectId, { text: trimmed, apply: false });
        setState({
          phase: 'review',
          text: trimmed,
          data: canonicaliseParsedData(result.data as JsonObject),
          assumptions: result.assumptions,
          edited: new Set(),
          warnings: result.warnings,
        });
      } catch (err) {
        const error = AppError.from(err);
        if (error.isAborted) return;
        setState({ phase: 'error', text: trimmed, error });
      }
    },
    [projectId],
  );

  // NOTE: both handlers read `state` from the closure and perform their side
  // effects (dispatch, toasts) OUTSIDE any React state updater — an updater
  // runs twice under StrictMode, and a dispatch inside one would apply the
  // brief twice.
  const editAssumption = useCallback(
    (field: string, raw: string): void => {
      if (state.phase !== 'review') return;
      const previous = getFieldValue(state.data, field);
      const value = parseFieldValue(field, previous, raw);
      if (value === undefined) {
        pushToast({
          tone: 'warning',
          title: `We couldn't read "${raw.trim()}" for ${fieldLabel(field).toLowerCase()}.`,
          description:
            'The assumed value is unchanged. Try a plain number, Yes/No, or 45L-style money.',
        });
        return;
      }
      const nextData = setBriefField(state.data, field, value);
      if (nextData === null) {
        pushToast({
          tone: 'warning',
          title: "That value can't be edited from the chip.",
          description:
            'Apply the brief, then change it on the form — everything stays editable there.',
        });
        return;
      }
      setState({ ...state, data: nextData, edited: new Set([...state.edited, field]) });
    },
    [state, pushToast],
  );

  const apply = useCallback((): void => {
    if (state.phase !== 'review') return;

    // vastuMode lives on BriefDoc, not in data — lift it onto the op's field.
    const { vastuMode: rawVastu, ...rest } = state.data;
    const vastuMode =
      typeof rawVastu === 'string' && (VASTU_MODES as readonly string[]).includes(rawVastu)
        ? (rawVastu as VastuMode)
        : undefined;
    // Both branches build on `rest`: a present-but-off-enum vastuMode string is
    // an untrusted value and must be DROPPED, never written into brief.data as
    // a stray key (vastuMode lives on BriefDoc, not in data).
    const patch = pruneUnchanged(
      vastuMode === undefined ? rest : { ...rest, vastuDecided: true },
      rawData,
    );

    if (Object.keys(patch).length === 0 && vastuMode === undefined) {
      pushToast({ tone: 'info', title: 'The brief already says all of this — nothing to apply.' });
      setState({ phase: 'idle' });
      return;
    }

    const result = update({
      patch,
      label: 'Brief filled from pasted text',
      ...(vastuMode === undefined ? {} : { vastuMode }),
    });

    if (!result.ok) {
      pushToast({
        tone: 'error',
        title: "We couldn't apply the parsed brief.",
        description:
          result.issues[0]?.message ?? 'The document may be out of sync — reload and retry.',
      });
      return;
    }

    pushToast({
      tone: 'success',
      title: 'Brief updated from your text — one change, one undo.',
      action: { label: 'Undo', run: () => void useModelStore.getState().undo() },
    });
    setText('');
    setState({ phase: 'idle' });
  }, [state, pushToast, rawData, update]);

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <Card className={className}>
      <CardHeader
        title="Paste the client's words"
        description="A WhatsApp message, an email, dictation — we read it into the form and show every assumption we make."
      />

      <div className="px-4 pb-4">
        <Textarea
          rows={4}
          value={text}
          placeholder={
            'e.g. "3BHK G+1 for a family of four, pooja room, covered parking, budget around 60 lakh, east entrance if possible"'
          }
          aria-label="Client brief text"
          disabled={state.phase === 'parsing'}
          onChange={(e) => setText(e.target.value)}
        />
        <div className="mt-2 flex items-center gap-3">
          <Button
            variant="primary"
            size="sm"
            iconLeft="sparkles"
            disabled={!ready || text.trim() === '' || state.phase === 'parsing'}
            loading={state.phase === 'parsing'}
            loadingLabel="Reading the brief…"
            onClick={() => void parse(text)}
          >
            Read this brief
          </Button>
          <span className="text-2xs text-ink-subtle">
            Nothing is applied until you review it. Dictation works too — use your keyboard&rsquo;s
            mic.
          </span>
        </div>
      </div>

      {state.phase === 'parsing' ? <ParsingSkeleton /> : null}
      {state.phase === 'error' ? (
        <ParseFailed error={state.error} onRetry={() => void parse(state.text)} />
      ) : null}
      {state.phase === 'review' ? (
        <ReviewPanel
          state={state}
          onEdit={editAssumption}
          onApply={apply}
          onDiscard={() => setState({ phase: 'idle' })}
        />
      ) : null}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Sub-views
// ---------------------------------------------------------------------------

function ParsingSkeleton(): JSX.Element {
  return (
    <SkeletonRegion
      label="Reading the brief and listing our assumptions"
      className="border-t border-line px-4 py-4"
    >
      <div className="flex flex-wrap gap-2">
        {/* Static class names — Tailwind cannot see interpolated widths. */}
        {['w-24', 'w-32', 'w-20', 'w-28', 'w-24'].map((w, i) => (
          <Skeleton key={i} shape="block" className={cn('h-7 rounded-full', w)} />
        ))}
      </div>
      <Skeleton className="mt-3 h-3 w-3/5" />
    </SkeletonRegion>
  );
}

function ParseFailed({ error, onRetry }: { error: AppError; onRetry: () => void }): JSX.Element {
  return (
    <div className="border-t border-line px-4 py-4" role="alert">
      <div className="flex items-start gap-2.5">
        <Icon name="alert-circle" size={16} className="mt-0.5 shrink-0 text-fail-ink" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-ink">We couldn&rsquo;t read the brief.</p>
          <p className="mt-0.5 text-xs leading-5 text-ink-muted">
            {error.message} {error.action}
          </p>
          {error.requestId === null ? null : (
            <p className="mt-1 font-mono text-2xs text-ink-subtle">Request {error.requestId}</p>
          )}
        </div>
        <Button size="sm" variant="secondary" iconLeft="refresh" onClick={onRetry}>
          Try again
        </Button>
      </div>
      <p className="mt-2 text-2xs text-ink-subtle">
        Your text is untouched above — nothing was applied to the brief.
      </p>
    </div>
  );
}

function ReviewPanel({
  state,
  onEdit,
  onApply,
  onDiscard,
}: {
  state: ReviewState;
  onEdit: (field: string, raw: string) => void;
  onApply: () => void;
  onDiscard: () => void;
}): JSX.Element {
  const brief = useMemo(() => readBriefData(state.data), [state.data]);
  const beds = bedroomRows(brief.rooms);
  const others = otherRooms(brief.rooms);

  return (
    <div className="border-t border-line">
      {/* What we understood */}
      <div className="px-4 pt-3">
        <h4 className="text-2xs font-semibold uppercase tracking-wider text-ink-subtle">
          What we understood
        </h4>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {beds.length > 0 ? (
            <Chip size="sm" icon={null} severity="neutral">
              {beds.length} bedroom{beds.length === 1 ? '' : 's'}
            </Chip>
          ) : null}
          {others.map((room) => (
            <Chip key={room.type} size="sm" icon={null} severity="neutral">
              {room.count > 1 ? `${room.count} × ` : ''}
              {roomTypeLabel(room.type)}
            </Chip>
          ))}
          {brief.storeys !== undefined ? (
            <Chip size="sm" icon={null} severity="neutral">
              {formatFieldValue('brief.storeys', brief.storeys)}
            </Chip>
          ) : null}
          {brief.budgetInr !== undefined ? (
            <Chip size="sm" icon={null} severity="neutral">
              {formatRupeesCompact(brief.budgetInr)}
            </Chip>
          ) : null}
        </div>
      </div>

      {/* Assumption chips — the golden-rule-4 surface */}
      <div className="px-4 pt-3">
        <h4 className="text-2xs font-semibold uppercase tracking-wider text-ink-subtle">
          What we assumed{state.assumptions.length > 0 ? ` (${state.assumptions.length})` : ''}
        </h4>
        {state.assumptions.length === 0 ? (
          <p className="mt-1.5 text-xs text-ink-muted">
            Nothing — everything above was stated in the text.
          </p>
        ) : (
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {state.assumptions.map((a) => (
              <AssumptionChip
                key={a.field}
                label={fieldLabel(a.field)}
                valueText={formatFieldValue(a.field, getFieldValue(state.data, a.field) ?? a.value)}
                reason={a.reason}
                cite={a.cite ?? undefined}
                accepted={state.edited.has(a.field)}
                onCommit={(raw) => onEdit(a.field, raw)}
              />
            ))}
          </div>
        )}
        <p className="mt-1.5 text-2xs text-ink-subtle">
          Click any value to change it before applying. Dashed chips are our guesses; solid ones are
          yours.
        </p>
      </div>

      {/* Parser questions worth relaying to the client */}
      {state.warnings.length > 0 ? (
        <div className="px-4 pt-3">
          <h4 className="text-2xs font-semibold uppercase tracking-wider text-ink-subtle">
            Worth asking the client
          </h4>
          <ul className="mt-1.5 space-y-1">
            {state.warnings.map((w) => (
              <li key={w} className="flex items-start gap-1.5 text-xs leading-5 text-ink-muted">
                <Icon name="info" size={13} className="mt-0.5 shrink-0 text-info-ink" />
                {w}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className={cn('mt-3 flex items-center gap-2 border-t border-line px-4 py-3')}>
        <Button variant="primary" size="sm" iconLeft="check" onClick={onApply}>
          Use this brief
        </Button>
        <Button variant="ghost" size="sm" onClick={onDiscard}>
          Discard
        </Button>
        <span className="ml-auto text-2xs text-ink-subtle">
          Applies as one change — one Cmd-Z brings the old brief back.
        </span>
      </div>
    </div>
  );
}

export default FreeTextParse;
