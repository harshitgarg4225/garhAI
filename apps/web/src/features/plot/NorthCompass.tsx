/**
 * NorthCompass — sets `plot.northDeg` (integer degrees, clockwise from +Y).
 *
 * Drag the dial to rotate; the op dispatches on RELEASE, so a drag is one undo
 * step, not three hundred. The degree readout is click-to-edit (§15 — numbers
 * editable everywhere), and the dial is keyboard-operable: arrows rotate the
 * preview by 5° (1° with Shift), Enter commits, Escape reverts.
 */

import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from 'react';

import { cn } from '@garh/ui';

import { normalizeNorthDeg } from './ops';
import { usePlotActions, usePlotDoc } from './usePlot';

export interface NorthCompassProps {
  /** Dial diameter in px. */
  size?: number | undefined;
  className?: string | undefined;
}

export function NorthCompass({ size = 88, className }: NorthCompassProps): JSX.Element {
  const { northDeg } = usePlotDoc();
  const actions = usePlotActions();

  /** Degrees shown while dragging / arrow-keying; null = show the model. */
  const [preview, setPreview] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const dialRef = useRef<SVGSVGElement>(null);
  const draggingRef = useRef(false);

  const shown = preview ?? northDeg;

  useEffect(() => {
    // Model moved underneath (undo, another tab) — drop a stale preview.
    if (!draggingRef.current) setPreview(null);
  }, [northDeg]);

  const commit = (deg: number): void => {
    const next = normalizeNorthDeg(deg);
    setPreview(null);
    if (next !== northDeg) actions.setNorth(next);
  };

  const degFromPointer = (e: ReactPointerEvent<SVGSVGElement>): number => {
    const el = dialRef.current;
    if (el === null) return shown;
    const rect = el.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const dx = e.clientX - cx;
    const dy = e.clientY - cy;
    if (dx === 0 && dy === 0) return shown;
    // Screen y grows downward; "up" is -dy. Clockwise-from-up == atan2(dx, -dy).
    return normalizeNorthDeg((Math.atan2(dx, -dy) * 180) / Math.PI);
  };

  const onPointerDown = (e: ReactPointerEvent<SVGSVGElement>): void => {
    e.preventDefault();
    draggingRef.current = true;
    e.currentTarget.setPointerCapture(e.pointerId);
    setPreview(degFromPointer(e));
  };
  const onPointerMove = (e: ReactPointerEvent<SVGSVGElement>): void => {
    if (!draggingRef.current) return;
    setPreview(degFromPointer(e));
  };
  const onPointerUp = (e: ReactPointerEvent<SVGSVGElement>): void => {
    if (!draggingRef.current) return;
    draggingRef.current = false;
    e.currentTarget.releasePointerCapture(e.pointerId);
    commit(degFromPointer(e));
  };

  const onDialKeyDown = (e: ReactKeyboardEvent<SVGSVGElement>): void => {
    const step = e.shiftKey ? 1 : 5;
    if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') {
      e.preventDefault();
      setPreview(normalizeNorthDeg(shown - step));
    } else if (e.key === 'ArrowRight' || e.key === 'ArrowUp') {
      e.preventDefault();
      setPreview(normalizeNorthDeg(shown + step));
    } else if (e.key === 'Enter' && preview !== null) {
      e.preventDefault();
      commit(preview);
    } else if (e.key === 'Escape' && preview !== null) {
      e.preventDefault();
      setPreview(null);
    }
  };

  const commitDraft = (): void => {
    setEditing(false);
    const text = draft.trim().replace(/[°º]\s*$/, '');
    if (text === '') return;
    if (!/^-?\d+(?:\.\d+)?$/.test(text)) return; // ignore garbage; the readout reverts
    commit(Number(text));
  };

  const half = size / 2;
  const r = half - 6;

  return (
    <div className={cn('flex flex-col items-center gap-1', className)}>
      <svg
        ref={dialRef}
        width={size}
        height={size}
        viewBox={`0 0 ${String(size)} ${String(size)}`}
        role="slider"
        aria-label="True north direction"
        aria-valuemin={0}
        aria-valuemax={359}
        aria-valuenow={shown}
        aria-valuetext={`North is ${String(shown)} degrees clockwise from the top of the plot`}
        tabIndex={0}
        className="garh-focus-ring cursor-grab touch-none rounded-full active:cursor-grabbing"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={() => {
          draggingRef.current = false;
          setPreview(null);
        }}
        onKeyDown={onDialKeyDown}
      >
        <circle cx={half} cy={half} r={r} className="fill-surface stroke-line" strokeWidth={1.5} />
        {/* Cardinal ticks, fixed to the dial. */}
        {[0, 90, 180, 270].map((tick) => {
          const rad = (tick * Math.PI) / 180;
          const x1 = half + Math.sin(rad) * (r - 5);
          const y1 = half - Math.cos(rad) * (r - 5);
          const x2 = half + Math.sin(rad) * r;
          const y2 = half - Math.cos(rad) * r;
          return (
            <line key={tick} x1={x1} y1={y1} x2={x2} y2={y2} className="stroke-line-strong" strokeWidth={1.5} />
          );
        })}
        {/* Needle + N label rotate together. */}
        <g transform={`rotate(${String(shown)} ${String(half)} ${String(half)})`}>
          <polygon
            points={`${String(half)},${String(half - r + 8)} ${String(half - 5)},${String(half + 6)} ${String(half + 5)},${String(half + 6)}`}
            className={preview === null ? 'fill-fail' : 'fill-brand'}
          />
          <text
            x={half}
            y={half - r + 20}
            textAnchor="middle"
            className="fill-ink select-none text-[10px] font-semibold"
          >
            N
          </text>
        </g>
        <circle cx={half} cy={half} r={2.5} className="fill-ink-subtle" />
      </svg>

      {editing ? (
        <input
          autoFocus
          value={draft}
          aria-label="North direction in degrees"
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commitDraft}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              commitDraft();
            } else if (e.key === 'Escape') {
              e.preventDefault();
              setEditing(false);
            }
          }}
          className="garh-focus-ring w-14 rounded-sm border border-line bg-surface px-1 py-0.5 text-center text-xs text-ink garh-nums"
        />
      ) : (
        <button
          type="button"
          onClick={() => {
            setDraft(String(shown));
            setEditing(true);
          }}
          title="Click to type an exact bearing"
          className="garh-focus-ring rounded-sm text-xs font-medium text-ink garh-nums hover:underline"
        >
          {shown}°
        </button>
      )}
    </div>
  );
}
