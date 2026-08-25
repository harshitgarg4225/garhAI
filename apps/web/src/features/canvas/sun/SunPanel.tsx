/**
 * SunPanel.tsx — the date/time scrubber and compass readout (§8, §15).
 *
 * DOM overlay, mounted by the page next to the canvas. Honest controls:
 *  - the date field is DD-MM-YYYY (§15 Indian dates) and rejects rather than
 *    guesses; the current valid value stays until a new valid one is typed;
 *  - the time slider is IST wall-clock minutes — scrubbing re-aims the light
 *    through `useSunStore` and nothing else (see `sunStore.ts`);
 *  - the city line names the centroid in use, and SAYS SO when it is the
 *    default because the plot has no city pack yet (assumptions are visible);
 *  - below the horizon the readout says "below horizon", not a dimmed lie.
 *
 * The compass draws in SCREEN orientation: model +Y is screen-up, so true
 * north sits `northDeg` clockwise from up, and the sun needle sits at the
 * model azimuth (true azimuth + northDeg). Rotate the plot's north and both
 * needles follow — same maths the light uses (`frame.ts`).
 */

import { useEffect, useState } from 'react';

import { Button, cn } from '@garh/ui';

import { useModelStore } from '../../../stores/model';
import { cityForPack, DEFAULT_CITY } from './cities';
import { formatDdMmYyyy, formatMinutes, parseDdMmYyyy } from './dateText';
import { compassLabel, computeSunFrame } from './frame';
import { seasonPresets, useSunStore } from './sunStore';

export interface SunPanelProps {
  className?: string | undefined;
}

export function SunPanel({ className }: SunPanelProps): JSX.Element {
  const day = useSunStore((s) => s.day);
  const minutesOfDay = useSunStore((s) => s.minutesOfDay);
  const setDay = useSunStore((s) => s.setDay);
  const setMinutesOfDay = useSunStore((s) => s.setMinutesOfDay);
  const setToNow = useSunStore((s) => s.setToNow);

  const cityPack = useModelStore((s) => s.doc.plot.regProfile.cityPack);
  const northDeg = useModelStore((s) => s.doc.plot.northDeg);

  const city = cityForPack(cityPack);
  const effectiveCity = city ?? DEFAULT_CITY;
  const frame = computeSunFrame(
    day,
    minutesOfDay,
    effectiveCity.latDeg,
    effectiveCity.lonDeg,
    northDeg,
  );

  // The date field edits a draft; only a valid DD-MM-YYYY commits.
  const [draft, setDraft] = useState(() => formatDdMmYyyy(day));
  const [draftBad, setDraftBad] = useState(false);
  useEffect(() => {
    setDraft(formatDdMmYyyy(day));
    setDraftBad(false);
  }, [day]);

  const commitDraft = (): void => {
    const parsed = parseDdMmYyyy(draft);
    if (parsed === null) {
      setDraftBad(true);
      return;
    }
    setDraftBad(false);
    setDay(parsed);
  };

  return (
    <div
      className={cn(
        'pointer-events-auto flex w-64 flex-col gap-2 rounded-md border border-line bg-surface/95 p-3 shadow-sm',
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-xs font-semibold text-ink">Sun</h3>
        <span className="text-2xs text-ink-subtle garh-nums">
          {effectiveCity.name}
          {city === null ? ' · assumed' : ''}
        </span>
      </div>
      {city === null ? (
        <p className="text-2xs leading-4 text-ink-subtle">
          No city pack on this plot yet — using {DEFAULT_CITY.name}&apos;s latitude. Set the city
          in Plot &amp; Rules to place the sun for real.
        </p>
      ) : null}

      {/* Date, DD-MM-YYYY */}
      <label className="block">
        <span className="mb-1 flex items-baseline justify-between text-2xs font-medium text-ink-muted">
          <span>Date</span>
          <span>DD-MM-YYYY</span>
        </span>
        <input
          className={cn(
            'garh-focus-ring w-full rounded border bg-surface px-2 py-1 text-xs text-ink garh-nums',
            draftBad ? 'border-red-500' : 'border-line',
          )}
          value={draft}
          inputMode="numeric"
          spellCheck={false}
          aria-invalid={draftBad}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commitDraft}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commitDraft();
            if (e.key === 'Escape') {
              setDraft(formatDdMmYyyy(day));
              setDraftBad(false);
            }
          }}
        />
        {draftBad ? (
          <span className="mt-0.5 block text-2xs text-red-600">
            Not a DD-MM-YYYY date — try 21-06-{String(day.year)}.
          </span>
        ) : null}
      </label>

      {/* Time scrubber — light-only updates, by design */}
      <label className="block">
        <span className="mb-1 flex items-baseline justify-between text-2xs font-medium text-ink-muted">
          <span>Time (IST)</span>
          <span className="text-ink garh-nums">{formatMinutes(minutesOfDay)}</span>
        </span>
        <input
          type="range"
          min={0}
          max={1439}
          step={5}
          value={minutesOfDay}
          className="w-full"
          aria-label="Time of day, IST"
          onChange={(e) => setMinutesOfDay(Number(e.target.value))}
        />
      </label>

      <div className="flex flex-wrap items-center gap-1">
        {seasonPresets(day.year).map((preset) => (
          <Button
            key={preset.label}
            size="sm"
            variant={
              day.month === preset.day.month && day.day === preset.day.day ? 'secondary' : 'ghost'
            }
            onClick={() => setDay(preset.day)}
          >
            {preset.label}
          </Button>
        ))}
        <Button size="sm" variant="ghost" onClick={() => setToNow()}>
          Now
        </Button>
      </div>

      {/* Compass readout */}
      <div className="flex items-center gap-3">
        <Compass northDeg={northDeg} sunModelAzimuthDeg={frame.modelAzimuthDeg} sunUp={frame.aboveHorizon} />
        <div className="flex flex-col text-2xs leading-4 text-ink-muted garh-nums">
          {frame.aboveHorizon ? (
            <>
              <span>
                Azimuth {Math.round(frame.solar.azimuthDeg)}°{' '}
                {compassLabel(frame.solar.azimuthDeg)}
              </span>
              <span>Elevation {frame.solar.apparentElevationDeg.toFixed(1)}°</span>
              <span className="text-ink-subtle">Shadows fall {compassLabel(frame.solar.azimuthDeg + 180)}</span>
            </>
          ) : (
            <span>Sun is below the horizon — scrub into daylight to cast shadows.</span>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Compass — one SVG, screen-oriented (see header)
// ---------------------------------------------------------------------------

interface CompassProps {
  northDeg: number;
  sunModelAzimuthDeg: number;
  sunUp: boolean;
}

function Compass({ northDeg, sunModelAzimuthDeg, sunUp }: CompassProps): JSX.Element {
  const r = 24;
  const c = 28;
  const northTip = polar(c, c, r - 4, northDeg);
  const sunTip = polar(c, c, r - 8, sunModelAzimuthDeg);
  return (
    <svg
      width={c * 2}
      height={c * 2}
      viewBox={`0 0 ${c * 2} ${c * 2}`}
      role="img"
      aria-label={`Compass: north ${Math.round(northDeg)} degrees from screen up; sun ${sunUp ? `at ${Math.round(sunModelAzimuthDeg)} degrees` : 'below horizon'}`}
      className="shrink-0"
    >
      <circle cx={c} cy={c} r={r} fill="none" stroke="currentColor" strokeOpacity={0.25} />
      {/* North needle */}
      <line x1={c} y1={c} x2={northTip.x} y2={northTip.y} stroke="currentColor" strokeWidth={1.5} />
      <text
        x={polar(c, c, r + 1, northDeg).x}
        y={polar(c, c, r + 1, northDeg).y}
        fontSize={7}
        textAnchor="middle"
        dominantBaseline="middle"
        fill="currentColor"
      >
        N
      </text>
      {/* Sun needle */}
      {sunUp ? (
        <>
          <line
            x1={c}
            y1={c}
            x2={sunTip.x}
            y2={sunTip.y}
            stroke="#d97706"
            strokeWidth={2}
            strokeLinecap="round"
          />
          <circle cx={sunTip.x} cy={sunTip.y} r={3} fill="#f59e0b" />
        </>
      ) : null}
      <circle cx={c} cy={c} r={1.5} fill="currentColor" />
    </svg>
  );
}

/** Point at `deg` CLOCKWISE from screen-up around (cx, cy). */
function polar(cx: number, cy: number, radius: number, deg: number): { x: number; y: number } {
  const rad = ((deg - 90) * Math.PI) / 180; // 0° = up, clockwise positive
  return { x: cx + radius * Math.cos(rad), y: cy + radius * Math.sin(rad) };
}
