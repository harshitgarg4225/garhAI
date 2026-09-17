/**
 * PlotReadouts — the numbers an architect checks a drawn plot against.
 *
 *   - perimeter, and every side as the deed would state it (length + bearing);
 *   - every diagonal (a quadrilateral's two; the fan for bigger rings), because
 *     "does the diagonal match the deed?" is the first thing a site engineer
 *     asks about an irregular plot;
 *   - the registered deed area (typed in sq ft, gaj / sq yd or sq m) beside the
 *     drawn area, with the difference and its percentage — municipal forms ask
 *     for both, and the smaller usually governs.
 *
 * Every number is measured from the folded document; this panel owns nothing
 * but the deed-area draft. The deed area is stored with the regulatory profile
 * (`rules.ts` says why) and written as one op.
 */

import { useState } from 'react';

import {
  formatArea,
  formatLength,
  formatPlotArea,
  parseAreaMm2,
  polygonAreaMm2,
  polygonPerimeterMm,
} from '@garh/model';
import { Button, Chip, Input, PanelSection, SkeletonText, cn } from '@garh/ui';

import { cornerLabel, edgeBearingDeg, formatBearingDms, ringDiagonals } from './deed';
import { edgeLengthMm } from './geometry';
import { readDeedAreaMm2, reconcileDeedArea } from './rules';
import { useModelReady, usePlotActions, usePlotDoc, useUnitsDisplay } from './usePlot';

/** Beyond this the drawn and registered areas are worth a scrutiny note. */
export const DEED_AREA_TOLERANCE_PCT = 2;

export interface PlotReadoutsProps {
  className?: string | undefined;
}

export function PlotReadouts({ className }: PlotReadoutsProps): JSX.Element {
  const ready = useModelReady();
  const plot = usePlotDoc();
  const display = useUnitsDisplay();
  const actions = usePlotActions();
  const [deedDraft, setDeedDraft] = useState<string | null>(null);
  const [deedError, setDeedError] = useState<string | null>(null);

  if (!ready) {
    return (
      <PanelSection title="Measurements" className={className ?? ''}>
        <SkeletonText lines={3} />
      </PanelSection>
    );
  }

  const boundary = plot.boundary;
  if (boundary.length < 3) {
    return (
      <PanelSection title="Measurements" className={className ?? ''}>
        <p className="text-xs text-ink-muted">
          Draw or import the boundary first — sides, diagonals, perimeter and the deed-area check
          appear here.
        </p>
      </PanelSection>
    );
  }

  const drawnMm2 = polygonAreaMm2(boundary);
  const deedMm2 = readDeedAreaMm2(plot.regProfile.overrides);
  const reconciliation = deedMm2 === null ? null : reconcileDeedArea(drawnMm2, deedMm2);
  const diagonals = ringDiagonals(boundary);

  const commitDeed = (): void => {
    const raw = (deedDraft ?? '').trim();
    setDeedDraft(null);
    if (raw === '') {
      setDeedError(null);
      return;
    }
    let mm2: number;
    try {
      mm2 = parseAreaMm2(raw, display === 'm' ? 'sqm' : 'sqft');
    } catch {
      setDeedError(`We couldn't read "${raw}" as an area. Try 1200 sq ft, 133 gaj or 111.5 sqm.`);
      return;
    }
    if (mm2 <= 0) {
      setDeedError('A deed area has to be a positive area.');
      return;
    }
    const result = actions.setDeedArea(mm2);
    setDeedError(result.ok ? null : (result.issues[0]?.message ?? 'That area was not accepted.'));
  };

  const tolerance = reconciliation === null ? null : Math.abs(reconciliation.differencePct);

  return (
    <PanelSection title="Measurements" className={className ?? ''}>
      <div className="flex flex-wrap gap-1.5">
        <span data-testid="readout-area">
          <Chip severity="neutral" size="sm" icon="ruler" className="garh-nums">
            {formatPlotArea(drawnMm2, display)}
          </Chip>
        </span>
        <span data-testid="readout-perimeter">
          <Chip severity="neutral" size="sm" className="garh-nums">
            {formatLength(polygonPerimeterMm(boundary), display)} around
          </Chip>
        </span>
      </div>

      <h4 className="mt-3 text-2xs font-semibold uppercase tracking-wider text-ink-subtle">
        Sides, as the deed states them
      </h4>
      <ul className="mt-1 space-y-0.5 text-xs text-ink garh-nums" data-testid="readout-sides">
        {boundary.map((_, i) => {
          const bearing = edgeBearingDeg(boundary, i, plot.northDeg);
          return (
            <li key={`side-${String(i)}`} className="flex justify-between gap-2">
              <span className="text-ink-muted">
                {cornerLabel(i)}–{cornerLabel((i + 1) % boundary.length)}
              </span>
              <span>
                {formatLength(edgeLengthMm(boundary, i), display)}
                {bearing === null ? '' : ` · ${formatBearingDms(bearing)}`}
              </span>
            </li>
          );
        })}
      </ul>

      {diagonals.length > 0 ? (
        <>
          <h4 className="mt-3 text-2xs font-semibold uppercase tracking-wider text-ink-subtle">
            Diagonals
          </h4>
          <ul
            className="mt-1 space-y-0.5 text-xs text-ink garh-nums"
            data-testid="readout-diagonals"
          >
            {diagonals.slice(0, 12).map((d) => (
              <li
                key={`diag-${String(d.from)}-${String(d.to)}`}
                className="flex justify-between gap-2"
              >
                <span className="text-ink-muted">
                  {cornerLabel(d.from)}–{cornerLabel(d.to)}
                </span>
                <span>{formatLength(d.lengthMm, display)}</span>
              </li>
            ))}
            {diagonals.length > 12 ? (
              <li className="text-ink-subtle">…and {diagonals.length - 12} more</li>
            ) : null}
          </ul>
        </>
      ) : null}

      <h4 className="mt-3 text-2xs font-semibold uppercase tracking-wider text-ink-subtle">
        Area as per the sale deed
      </h4>
      <div className="mt-1 flex flex-wrap items-center gap-2">
        {deedDraft === null ? (
          <button
            type="button"
            onClick={() => {
              setDeedDraft(deedMm2 === null ? '' : formatArea(deedMm2, display));
            }}
            className="garh-focus-ring rounded-sm text-xs text-ink underline-offset-2 hover:underline garh-nums"
            data-testid="deed-area-value"
          >
            {deedMm2 === null ? 'Not entered — click to type it' : formatPlotArea(deedMm2, display)}
          </button>
        ) : (
          <Input
            autoFocus
            aria-label="Area as per the sale deed"
            value={deedDraft}
            placeholder={display === 'm' ? '111.5 sqm' : '1200 sq ft / 133 gaj'}
            onChange={(e) => setDeedDraft(e.target.value)}
            onBlur={commitDeed}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                commitDeed();
              } else if (e.key === 'Escape') {
                e.preventDefault();
                setDeedDraft(null);
              }
            }}
            className="w-44"
          />
        )}
        {deedMm2 !== null && deedDraft === null ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              const result = actions.setDeedArea(null);
              if (!result.ok) setDeedError(result.issues[0]?.message ?? 'Could not clear it.');
            }}
          >
            Clear
          </Button>
        ) : null}
      </div>
      {deedError === null ? null : <p className="mt-1 text-xs text-fail-ink">{deedError}</p>}

      {reconciliation !== null && tolerance !== null ? (
        <div className="mt-2" data-testid="deed-reconciliation">
          <Chip
            severity={tolerance > DEED_AREA_TOLERANCE_PCT ? 'warn' : 'pass'}
            size="sm"
            icon={tolerance > DEED_AREA_TOLERANCE_PCT ? 'alert-triangle' : 'check'}
            className="garh-nums"
          >
            Drawn {reconciliation.differenceMm2 >= 0 ? 'exceeds' : 'is short of'} the deed by{' '}
            {formatArea(Math.abs(reconciliation.differenceMm2), display)} (
            {reconciliation.differencePct >= 0 ? '+' : '−'}
            {String(Math.abs(reconciliation.differencePct))}%)
          </Chip>
          <p className={cn('mt-1 text-2xs leading-4 text-ink-subtle')}>
            {tolerance > DEED_AREA_TOLERANCE_PCT
              ? 'More than 2% apart. Check the deed sides against the survey before the tables are run — most bye-laws take the smaller area for FAR and coverage.'
              : 'Within 2% of the registered area.'}
          </p>
        </div>
      ) : null}
    </PanelSection>
  );
}
