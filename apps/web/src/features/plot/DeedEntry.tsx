/**
 * DeedEntry — type the plot the way the sale deed states it.
 *
 * Two modes, both pure math in `deed.ts` with this file owning only the form
 * state and the op dispatch:
 *
 *   Sides + diagonal   N side lengths in corner order (A→B→C…) plus the N−3
 *                      diagonals from corner A. For a quadrilateral that is the
 *                      four sides and A–C; the deed's other diagonal (B–D) can
 *                      be typed as a CHECK and the form reports the discrepancy
 *                      rather than hiding it.
 *   Bearings           the surveyor's field book: a whole-circle bearing and a
 *                      length per leg. The misclosure is shown as "1 in N"
 *                      before anything is committed; the ring is closed by the
 *                      compass rule; a gross misclosure is refused.
 *
 * The preview re-measures the ROUNDED ring, so every number shown is the one
 * that will be stored — never the request. Commit is one op group (boundary
 * + roads carried across, + north for a traverse), so one undo returns the
 * previous plot.
 */

import { useMemo, useState } from 'react';

import { formatLength, formatPlotArea } from '@garh/model';
import { Button, Chip, Input, LengthInput, SelectField, Tabs, cn } from '@garh/ui';

import {
  cornerLabel,
  formatBearingDms,
  parseBearingDeg,
  ringFromSidesAndDiagonals,
  ringFromTraverse,
  type DeedResult,
  type TraverseResult,
} from './deed';
import { usePlotActions, useUnitsDisplay } from './usePlot';

export type DeedEntryMode = 'sides' | 'bearings';

export interface DeedEntryProps {
  /** Called after the boundary op is accepted (e.g. to close a dialog). */
  onCreated?: (() => void) | undefined;
  initialMode?: DeedEntryMode | undefined;
  /** Label for the commit button — "Create boundary" or "Replace boundary". */
  commitLabel?: string | undefined;
  className?: string | undefined;
}

const MODE_ITEMS = [
  { value: 'sides', label: 'Sides + diagonal' },
  { value: 'bearings', label: 'Bearings' },
] as const;

const CORNER_OPTIONS = [3, 4, 5, 6, 7, 8].map((n) => ({
  value: String(n),
  label: `${String(n)} corners`,
}));

function resize<T>(list: readonly T[], n: number, fill: T): T[] {
  const out = list.slice(0, n);
  while (out.length < n) out.push(fill);
  return out;
}

export function DeedEntry({
  onCreated,
  initialMode = 'sides',
  commitLabel = 'Create boundary',
  className,
}: DeedEntryProps): JSX.Element {
  const [mode, setMode] = useState<DeedEntryMode>(initialMode);
  return (
    <div className={className} data-testid="deed-entry">
      <Tabs
        items={MODE_ITEMS}
        value={mode}
        onValueChange={(v) => setMode(v)}
        label="How the deed states the plot"
        variant="pill"
      />
      {mode === 'sides' ? (
        <SidesForm onCreated={onCreated} commitLabel={commitLabel} />
      ) : (
        <BearingsForm onCreated={onCreated} commitLabel={commitLabel} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sides + diagonal
// ---------------------------------------------------------------------------

function SidesForm({
  onCreated,
  commitLabel,
}: {
  onCreated: (() => void) | undefined;
  commitLabel: string;
}): JSX.Element {
  const display = useUnitsDisplay();
  const actions = usePlotActions();
  const [corners, setCorners] = useState(4);
  const [sides, setSides] = useState<(number | null)[]>([9144, 12192, 9144, 12192]);
  const [diagonals, setDiagonals] = useState<(number | null)[]>([15240]);
  const [checkDiagonal, setCheckDiagonal] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const setCornerCount = (n: number): void => {
    setCorners(n);
    setSides((s) => resize(s, n, null));
    setDiagonals((d) => resize(d, Math.max(0, n - 3), null));
    if (n !== 4) setCheckDiagonal(null);
  };

  const complete = sides.every((s) => s !== null) && diagonals.every((d) => d !== null);
  const result: DeedResult | null = useMemo(() => {
    if (!complete) return null;
    return ringFromSidesAndDiagonals(
      sides.map((s) => s ?? 0),
      diagonals.map((d) => d ?? 0),
    );
  }, [complete, sides, diagonals]);

  const bd =
    result !== null && result.ok && corners === 4
      ? (result.diagonals.find((d) => d.from === 1 && d.to === 3)?.lengthMm ?? null)
      : null;

  const create = (): void => {
    if (!result?.ok) return;
    const dispatched = actions.setBoundary(result.polygon, {
      label: 'Plot from deed',
      source: 'manual',
    });
    if (!dispatched.ok) {
      setError(dispatched.issues[0]?.message ?? 'That boundary was not accepted.');
      return;
    }
    setError(null);
    onCreated?.();
  };

  return (
    <div className="mt-3 flex flex-col gap-3" data-testid="deed-sides">
      <div className="flex flex-wrap items-end gap-3">
        <SelectField
          label="Corners"
          value={String(corners)}
          onValueChange={(v) => setCornerCount(Number(v))}
          options={CORNER_OPTIONS}
          className="w-32"
        />
        <p className="max-w-md text-2xs leading-4 text-ink-subtle">
          Type the sides in order round the plot (A→B→C…). Side A–B is drawn along the bottom, left
          to right — mark it as the road afterwards if that is where the road is. Diagonals are
          measured from corner A.
        </p>
      </div>

      <div className="grid gap-2 sm:grid-cols-2">
        {sides.map((value, i) => (
          <LengthInput
            key={`side-${String(i)}`}
            label={`Side ${cornerLabel(i)}–${cornerLabel((i + 1) % corners)}`}
            valueMm={value}
            onCommitMm={(mm) => setSides((s) => s.map((v, k) => (k === i ? mm : v)))}
            display={display}
            minMm={100}
            maxMm={500_000}
            hideMmHint
          />
        ))}
        {diagonals.map((value, i) => (
          <LengthInput
            key={`diag-${String(i)}`}
            label={`Diagonal A–${cornerLabel(i + 2)}`}
            valueMm={value}
            onCommitMm={(mm) => setDiagonals((d) => d.map((v, k) => (k === i ? mm : v)))}
            display={display}
            minMm={100}
            maxMm={500_000}
            hideMmHint
          />
        ))}
        {corners === 4 ? (
          <LengthInput
            label="Check: diagonal B–D (optional)"
            valueMm={checkDiagonal}
            onCommitMm={setCheckDiagonal}
            display={display}
            minMm={100}
            maxMm={500_000}
            hideMmHint
            placeholder="from the deed, if given"
          />
        ) : null}
      </div>

      <DeedPreview result={result} />

      {bd !== null && checkDiagonal !== null ? (
        <Chip
          severity={Math.abs(bd - checkDiagonal) <= 50 ? 'pass' : 'warn'}
          size="sm"
          className="garh-nums self-start"
          title="deed-check-diagonal"
        >
          Deed says B–D {formatLength(checkDiagonal, display)}; these figures give{' '}
          {formatLength(bd, display)} ({bd >= checkDiagonal ? '+' : '−'}
          {formatLength(Math.abs(bd - checkDiagonal), display)}).
          {Math.abs(bd - checkDiagonal) > 50 ? ' One of the deed figures is off.' : ''}
        </Chip>
      ) : null}

      {error === null ? null : <p className="text-xs text-fail-ink">{error}</p>}

      <div>
        <Button
          variant="primary"
          size="md"
          onClick={create}
          disabled={!result?.ok}
          data-testid="deed-create"
        >
          {commitLabel}
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bearings
// ---------------------------------------------------------------------------

interface LegDraft {
  readonly bearingText: string;
  readonly lengthMm: number | null;
}

const DEFAULT_LEGS: LegDraft[] = [
  { bearingText: '90', lengthMm: 9144 },
  { bearingText: '0', lengthMm: 12192 },
  { bearingText: '270', lengthMm: 9144 },
  { bearingText: '180', lengthMm: 12192 },
];

function BearingsForm({
  onCreated,
  commitLabel,
}: {
  onCreated: (() => void) | undefined;
  commitLabel: string;
}): JSX.Element {
  const display = useUnitsDisplay();
  const actions = usePlotActions();
  const [legs, setLegs] = useState<LegDraft[]>(DEFAULT_LEGS);
  const [error, setError] = useState<string | null>(null);

  const parsed = legs.map((leg) => ({
    bearingDeg: parseBearingDeg(leg.bearingText),
    lengthMm: leg.lengthMm,
  }));
  const complete = parsed.every((p) => p.bearingDeg !== null && p.lengthMm !== null);
  const result: TraverseResult | null = useMemo(() => {
    if (!complete) return null;
    return ringFromTraverse(
      parsed.map((p) => ({ bearingDeg: p.bearingDeg ?? 0, lengthMm: p.lengthMm ?? 0 })),
    );
    // parsed is derived from legs; legs is the real dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [complete, legs]);

  const update = (i: number, patch: Partial<LegDraft>): void =>
    setLegs((list) => list.map((leg, k) => (k === i ? { ...leg, ...patch } : leg)));

  const create = (): void => {
    if (!result?.ok) return;
    const dispatched = actions.setBoundary(result.polygon, {
      label: 'Plot from bearings',
      source: 'manual',
      // Built north-up: the compass must agree, in the same undo step.
      northDeg: 0,
    });
    if (!dispatched.ok) {
      setError(dispatched.issues[0]?.message ?? 'That boundary was not accepted.');
      return;
    }
    setError(null);
    onCreated?.();
  };

  return (
    <div className="mt-3 flex flex-col gap-3" data-testid="deed-bearings">
      <p className="text-2xs leading-4 text-ink-subtle">
        One leg per side, as the field book reads: whole-circle bearing (clockwise from north — 135,
        135°30&apos;, or N45°E) and length. Read the deed either way round; the ring is stored
        anticlockwise and north is set to the top of the plot.
      </p>

      <ol className="flex flex-col gap-2">
        {legs.map((leg, i) => {
          const deg = parsed[i]?.bearingDeg ?? null;
          return (
            <li key={`leg-${String(i)}`} className="flex flex-wrap items-end gap-2">
              <span className="w-12 pb-2 text-xs text-ink-subtle">
                {cornerLabel(i)}–{cornerLabel((i + 1) % legs.length)}
              </span>
              <div className="w-36">
                <label className="block text-2xs font-medium text-ink-muted">
                  Bearing {String(i + 1)}
                  <Input
                    aria-label={`Bearing of leg ${String(i + 1)}`}
                    value={leg.bearingText}
                    onChange={(e) => update(i, { bearingText: e.target.value })}
                    placeholder="135°30'"
                    invalid={leg.bearingText.trim() !== '' && deg === null}
                    className="mt-1"
                  />
                </label>
                <span className="text-2xs text-ink-subtle garh-nums">
                  {deg === null
                    ? leg.bearingText.trim() === ''
                      ? ' '
                      : 'not a bearing'
                    : formatBearingDms(deg)}
                </span>
              </div>
              <LengthInput
                label={`Length of leg ${String(i + 1)}`}
                valueMm={leg.lengthMm}
                onCommitMm={(mm) => update(i, { lengthMm: mm })}
                display={display}
                minMm={100}
                maxMm={500_000}
                hideMmHint
                className="w-36"
              />
              <Button
                variant="ghost"
                size="sm"
                iconLeft="trash"
                aria-label={`Remove leg ${String(i + 1)}`}
                disabled={legs.length <= 3}
                onClick={() => setLegs((list) => list.filter((_, k) => k !== i))}
              >
                Remove
              </Button>
            </li>
          );
        })}
      </ol>
      <div>
        <Button
          variant="secondary"
          size="sm"
          iconLeft="plus"
          disabled={legs.length >= 24}
          onClick={() => setLegs((list) => [...list, { bearingText: '', lengthMm: null }])}
        >
          Add a leg
        </Button>
      </div>

      <DeedPreview result={result} />
      {result !== null && (result.ok || result.closure !== undefined) ? (
        <ClosureNote result={result} />
      ) : null}

      {error === null ? null : <p className="text-xs text-fail-ink">{error}</p>}

      <div>
        <Button
          variant="primary"
          size="md"
          onClick={create}
          disabled={!result?.ok}
          data-testid="deed-create"
        >
          {commitLabel}
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared preview
// ---------------------------------------------------------------------------

function DeedPreview({ result }: { result: DeedResult | null }): JSX.Element | null {
  const display = useUnitsDisplay();
  if (result === null) {
    return (
      <p className="text-xs text-ink-subtle" data-testid="deed-preview-pending">
        Fill in every length to see the plot.
      </p>
    );
  }
  if (!result.ok) {
    return (
      <p role="alert" className="text-xs text-fail-ink" data-testid="deed-preview-error">
        {result.reason}
      </p>
    );
  }
  const rounded = result.sides.filter((s) => s.achievedMm !== s.requestedMm);
  return (
    <div className="flex flex-col gap-1.5" data-testid="deed-preview">
      <div className="flex flex-wrap gap-1.5">
        <Chip severity="neutral" size="sm" icon="ruler" className="garh-nums">
          {formatPlotArea(result.areaMm2, display)}
        </Chip>
        <Chip severity="neutral" size="sm" className="garh-nums">
          {formatLength(result.perimeterMm, display)} around
        </Chip>
        {result.diagonals.map((d) => (
          <Chip
            key={`${String(d.from)}-${String(d.to)}`}
            severity="neutral"
            size="sm"
            className="garh-nums"
          >
            {cornerLabel(d.from)}–{cornerLabel(d.to)} {formatLength(d.lengthMm, display)}
          </Chip>
        ))}
      </div>
      {rounded.length > 0 ? (
        <p className={cn('text-2xs leading-4 text-ink-subtle garh-nums')}>
          Rounded to whole millimetres:{' '}
          {rounded
            .map(
              (s) =>
                `${cornerLabel(s.edgeIndex)}–${cornerLabel((s.edgeIndex + 1) % result.sides.length)} ${String(s.requestedMm)} → ${String(s.achievedMm)} mm`,
            )
            .join(', ')}
          .
        </p>
      ) : null}
    </div>
  );
}

function ClosureNote({ result }: { result: TraverseResult }): JSX.Element | null {
  const closure = result.ok ? result.closure : result.closure;
  if (closure === undefined) return null;
  if (closure.misclosureMm === 0) {
    return (
      <span data-testid="deed-closure" className="self-start">
        <Chip severity="pass" size="sm">
          Closes exactly{closure.reversed ? ' (read clockwise — stored anticlockwise)' : ''}.
        </Chip>
      </span>
    );
  }
  return (
    <span data-testid="deed-closure" className="self-start">
      <Chip severity={result.ok ? 'info' : 'fail'} size="sm" className="garh-nums">
        Misclosure {String(closure.misclosureMm)} mm — 1 in{' '}
        {String(closure.precisionDenominator ?? 0)}
        {result.ok ? ', distributed round the ring by the compass rule' : ''}
        {closure.reversed ? '; read clockwise, stored anticlockwise' : ''}.
      </Chip>
    </span>
  );
}
