/**
 * AreaReadout — "1,200.0 sq ft · 133 gaj", live from the folded document.
 *
 * §15's Indian-defaults rule verbatim: ft-in (or m²) primary with gaj
 * alongside, because a plot in north India is bought and argued about in gaj.
 * Formatting comes from the units module; this component owns no numbers.
 */

import { formatPlotArea, polygonAreaMm2, polygonPerimeterMm, formatLength } from '@garh/model';
import { Chip, cn } from '@garh/ui';

import { usePlotDoc, useUnitsDisplay } from './usePlot';

export interface AreaReadoutProps {
  /** Also show the boundary perimeter. Default false. */
  withPerimeter?: boolean | undefined;
  className?: string | undefined;
}

export function AreaReadout({ withPerimeter = false, className }: AreaReadoutProps): JSX.Element | null {
  const plot = usePlotDoc();
  const display = useUnitsDisplay();

  if (plot.boundary.length < 3) return null;

  const areaMm2 = polygonAreaMm2(plot.boundary);
  return (
    <span className={cn('inline-flex items-center gap-1.5', className)}>
      <Chip severity="neutral" icon="ruler" className="garh-nums">
        {formatPlotArea(areaMm2, display)}
      </Chip>
      {withPerimeter ? (
        <Chip severity="neutral" size="sm" className="garh-nums">
          {formatLength(polygonPerimeterMm(plot.boundary), display)} around
        </Chip>
      ) : null}
    </span>
  );
}
