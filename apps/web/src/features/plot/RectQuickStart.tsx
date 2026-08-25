/**
 * RectQuickStart — "30 × 40 ft" in, rectangle boundary out.
 *
 * Most Indian residential plots are quoted as width × depth, so this is the
 * fast path a new project starts on. Both fields go through `LengthInput`
 * (mm in, pretty out); a bare number means feet in a ft-in project and metres
 * in a metric one, exactly as it does everywhere else.
 */

import { useState } from 'react';

import { formatPlotArea } from '@garh/model';
import { Button, LengthInput } from '@garh/ui';

import { rectBoundaryMm } from './geometry';
import { usePlotActions, useUnitsDisplay } from './usePlot';

/** 30 × 40 ft in mm — the classic site, offered as a starting point. */
const DEFAULT_WIDTH_MM = 9144;
const DEFAULT_DEPTH_MM = 12192;

export interface RectQuickStartProps {
  /** Called after the boundary op is accepted (e.g. to close a dialog). */
  onCreated?: (() => void) | undefined;
  className?: string | undefined;
}

export function RectQuickStart({ onCreated, className }: RectQuickStartProps): JSX.Element {
  const display = useUnitsDisplay();
  const actions = usePlotActions();

  const [widthMm, setWidthMm] = useState<number>(DEFAULT_WIDTH_MM);
  const [depthMm, setDepthMm] = useState<number>(DEFAULT_DEPTH_MM);
  const [error, setError] = useState<string | null>(null);

  const create = (): void => {
    const result = actions.setBoundary(rectBoundaryMm(widthMm, depthMm), {
      label: 'Plot boundary',
      source: 'manual',
    });
    if (!result.ok) {
      setError(result.issues[0]?.message ?? 'That boundary was not accepted. Check the sizes.');
      return;
    }
    setError(null);
    onCreated?.();
  };

  return (
    <div className={className}>
      <div className="flex flex-wrap items-end gap-3">
        <LengthInput
          label="Plot width"
          valueMm={widthMm}
          onCommitMm={setWidthMm}
          display={display}
          minMm={1000}
          maxMm={200_000}
          className="w-36"
        />
        <span className="pb-2 text-sm text-ink-subtle" aria-hidden="true">
          ×
        </span>
        <LengthInput
          label="Plot depth"
          valueMm={depthMm}
          onCommitMm={setDepthMm}
          display={display}
          minMm={1000}
          maxMm={200_000}
          className="w-36"
        />
        <Button variant="primary" size="md" onClick={create} className="mb-0.5">
          Create boundary
        </Button>
      </div>
      <p className="mt-2 text-xs text-ink-muted garh-nums">
        {formatPlotArea(widthMm * depthMm, display)} — drag any corner afterwards, or click an edge
        length to type an exact one.
      </p>
      {error === null ? null : <p className="mt-1 text-xs text-fail-ink">{error}</p>}
    </div>
  );
}
