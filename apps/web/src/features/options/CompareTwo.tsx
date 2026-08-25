/**
 * CompareTwo — §15's side-by-side of any two options, differences highlighted:
 * rooms present in one and not the other, per-component score deltas, built-up
 * and circulation deltas. All the arithmetic lives in stats.compareOptions();
 * this file only renders its output.
 */

import { Button, Icon, cn } from '@garh/ui';
import { formatSqft } from '@garh/model';

import { MiniPlanSvg } from './MiniPlanSvg';
import { labelForRoomType, miniPlanFromOption } from './planGeometry';
import { compareOptions } from './stats';
import type { PlanOption, PtMm } from './types';

export interface CompareTwoProps {
  readonly a: PlanOption;
  readonly b: PlanOption;
  /** Display indices ("Option 2"), aligned with the options grid. */
  readonly indexA: number;
  readonly indexB: number;
  readonly outline?: readonly PtMm[] | undefined;
  readonly onClose: () => void;
  readonly onApply: (option: PlanOption, optionIndex: number) => void;
  readonly className?: string | undefined;
}

export function CompareTwo({
  a,
  b,
  indexA,
  indexB,
  outline,
  onClose,
  onApply,
  className,
}: CompareTwoProps): JSX.Element {
  const diff = compareOptions(a, b);

  return (
    <section
      aria-label={`Comparing option ${indexA + 1} and option ${indexB + 1}`}
      className={cn('space-y-4 rounded-lg border border-line bg-surface p-4', className)}
    >
      <header className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">
          Option {indexA + 1} vs Option {indexB + 1}
        </h3>
        <Button variant="ghost" size="sm" onClick={onClose}>
          <Icon name="x" size={14} /> Close
        </Button>
      </header>

      <div className="grid grid-cols-2 gap-4">
        {[
          { option: a, index: indexA },
          { option: b, index: indexB },
        ].map(({ option, index }) => (
          <div key={option.id} className="space-y-2">
            <MiniPlanSvg
              geometry={miniPlanFromOption(option)}
              outline={outline}
              label={`Option ${index + 1} floor plan`}
            />
            <div className="flex items-center justify-between">
              <p className="garh-nums text-xs text-ink-muted">{formatSqft(option.builtUpMm2, 0)}</p>
              <Button variant="secondary" size="sm" onClick={() => onApply(option, index)}>
                Use this plan
              </Button>
            </div>
          </div>
        ))}
      </div>

      {diff.rooms.onlyA.length > 0 || diff.rooms.onlyB.length > 0 ? (
        <div className="grid grid-cols-2 gap-4 text-xs">
          <RoomDiffList
            title={`Only in Option ${indexA + 1}`}
            roomTypes={diff.rooms.onlyA}
          />
          <RoomDiffList
            title={`Only in Option ${indexB + 1}`}
            roomTypes={diff.rooms.onlyB}
          />
        </div>
      ) : (
        <p className="text-xs text-ink-subtle">Both plans contain the same set of rooms.</p>
      )}

      <table className="w-full text-xs">
        <caption className="sr-only">Score comparison</caption>
        <thead>
          <tr className="text-left text-2xs uppercase tracking-wide text-ink-subtle">
            <th className="py-1 font-medium">Score</th>
            <th className="py-1 text-right font-medium">Option {indexA + 1}</th>
            <th className="py-1 text-right font-medium">Option {indexB + 1}</th>
          </tr>
        </thead>
        <tbody>
          {diff.scores.map((row) => (
            <tr key={row.key} className="border-t border-line">
              <td className="py-1 text-ink-muted">{row.label}</td>
              <ScoreCell value={row.a} winner={row.delta < 0} />
              <ScoreCell value={row.b} winner={row.delta > 0} />
            </tr>
          ))}
        </tbody>
      </table>

      <p className="text-2xs text-ink-subtle">
        {diff.sameStairAnchor
          ? 'Both plans place the staircase at the same anchor.'
          : 'The two plans use different staircase positions.'}
        {diff.circulationDelta !== 0
          ? ` Circulation differs by ${Math.abs(diff.circulationDelta)} percentage point${Math.abs(diff.circulationDelta) === 1 ? '' : 's'}.`
          : ''}
      </p>
    </section>
  );
}

function RoomDiffList({
  title,
  roomTypes,
}: {
  title: string;
  roomTypes: readonly string[];
}): JSX.Element {
  return (
    <div>
      <p className="mb-1 font-medium text-ink-muted">{title}</p>
      {roomTypes.length === 0 ? (
        <p className="text-ink-subtle">Nothing extra</p>
      ) : (
        <ul className="space-y-0.5 text-ink">
          {roomTypes.map((type, i) => (
            <li key={`${type}-${i}`} className="flex items-center gap-1">
              <Icon name="plus" size={10} className="text-ink-subtle" />
              {labelForRoomType(type)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ScoreCell({ value, winner }: { value: number; winner: boolean }): JSX.Element {
  return (
    <td
      className={cn(
        'garh-nums py-1 text-right',
        winner ? 'font-semibold text-pass-ink' : 'text-ink',
      )}
    >
      {value}
    </td>
  );
}
