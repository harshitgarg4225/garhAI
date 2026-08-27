/**
 * PlotEditor — the F1 boundary editing surface.
 *
 * An SVG canvas, deliberately: the shared R3F canvas is the PHASE 4 wall
 * editor, and the plot surface has different needs (a handful of vertices,
 * heavy text interaction, no 60fps mesh churn). SVG keeps every dimension
 * label a real, focusable element — which is what §15's "numbers editable
 * everywhere" costs on a WebGL canvas and gets for free here.
 *
 * All rendering happens in MODEL millimetres via the viewBox; only stored
 * geometry keeps the integer discipline — label anchors and normals may be
 * floats because they never leave the screen.
 *
 * State discipline (golden rule 1): this component owns nothing but the
 * in-flight drag preview and the label being edited. Every committed change
 * is an op dispatch; undo/redo come from the model store for free.
 */

import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from 'react';

import {
  formatLength,
  formatPlotArea,
  polygonAreaMm2,
  tryParseLengthMm,
  type Polygon,
  type Pt,
} from '@garh/model';
import { Button, Chip, EmptyState, SkeletonCanvas, cn } from '@garh/ui';

import { SNAP_COARSE_MM, SNAP_FINE_MM, snapMm } from '../../lib/units';
import {
  checkBoundary,
  edgeFacing,
  edgeLengthMm,
  frontEdgeIndex,
  insertVertexOnEdge,
  moveVertex,
  remapRoadsAfterInsert,
  remapRoadsAfterRemove,
  removeVertex,
  ringAt,
  setEdgeLengthMm,
} from './geometry';
import { NorthCompass } from './NorthCompass';
import { RectQuickStart } from './RectQuickStart';
import { useModelReady, usePlotActions, usePlotDoc, useUnitsDisplay } from './usePlot';

// ---------------------------------------------------------------------------
// Viewport math (frozen to the COMMITTED boundary, so a drag cannot move the
// camera it is being measured against)
// ---------------------------------------------------------------------------

interface Viewport {
  readonly minX: number;
  readonly maxY: number;
  readonly pad: number;
  readonly vbW: number;
  readonly vbH: number;
  readonly span: number;
}

function makeViewport(boundary: Polygon): Viewport {
  let minX = 0;
  let minY = 0;
  let maxX = 9144;
  let maxY = 12192;
  if (boundary.length > 0) {
    minX = Math.min(...boundary.map((p) => p.x));
    minY = Math.min(...boundary.map((p) => p.y));
    maxX = Math.max(...boundary.map((p) => p.x));
    maxY = Math.max(...boundary.map((p) => p.y));
  }
  const span = Math.max(maxX - minX, maxY - minY, 1000);
  const pad = Math.max(2500, Math.round(span / 5));
  return { minX, maxY, pad, vbW: maxX - minX + 2 * pad, vbH: maxY - minY + 2 * pad, span };
}

/** Model mm -> SVG user units (y flipped: model +Y is north/up). */
function toSvg(vp: Viewport, p: Pt): { x: number; y: number } {
  return { x: p.x - vp.minX + vp.pad, y: vp.maxY - p.y + vp.pad };
}

/** Pointer event -> model mm, honouring preserveAspectRatio="xMidYMid meet". */
function clientToModel(vp: Viewport, svg: SVGSVGElement, clientX: number, clientY: number): Pt {
  const rect = svg.getBoundingClientRect();
  const scale = Math.min(rect.width / vp.vbW, rect.height / vp.vbH);
  const ox = (rect.width - vp.vbW * scale) / 2;
  const oy = (rect.height - vp.vbH * scale) / 2;
  const xvb = (clientX - rect.left - ox) / scale;
  const yvb = (clientY - rect.top - oy) / scale;
  return {
    x: Math.round(xvb - vp.pad + vp.minX),
    y: Math.round(vp.maxY - (yvb - vp.pad)),
  };
}

/** Grid step that yields a readable line count for the current span. */
function gridStep(span: number): number {
  for (const step of [500, 1000, 2000, 5000, 10000]) {
    if (span / step <= 40) return step;
  }
  return 20000;
}

/** Float outward normal (unit) of edge i for a CCW ring. Rendering only. */
function outwardNormal(boundary: Polygon, i: number): { x: number; y: number } {
  const a = ringAt(boundary, i);
  const b = ringAt(boundary, i + 1);
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy);
  if (len === 0) return { x: 0, y: 0 };
  // Right side of a->b: outward for CCW (the model's storage convention).
  return { x: dy / len, y: -dx / len };
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface DragState {
  readonly vertexIndex: number;
  readonly current: Pt;
}

interface Notice {
  readonly tone: 'error' | 'info';
  readonly text: string;
}

export interface PlotEditorProps {
  className?: string | undefined;
}

export function PlotEditor({ className }: PlotEditorProps): JSX.Element {
  const ready = useModelReady();
  const plot = usePlotDoc();
  const display = useUnitsDisplay();
  const actions = usePlotActions();

  const svgRef = useRef<SVGSVGElement>(null);
  const [drag, setDrag] = useState<DragState | null>(null);
  const [selectedVertex, setSelectedVertex] = useState<number | null>(null);
  const [editingEdge, setEditingEdge] = useState<number | null>(null);
  const [edgeDraft, setEdgeDraft] = useState('');
  const [notice, setNotice] = useState<Notice | null>(null);

  const boundary = plot.boundary;

  // The document changed under us (undo, rebase, another tab): any in-flight
  // interaction refers to indices that may no longer exist.
  useEffect(() => {
    setDrag(null);
    setEditingEdge(null);
    setSelectedVertex((v) => (v !== null && v < boundary.length ? v : null));
  }, [boundary]);

  if (!ready) return <SkeletonCanvas className={cn('min-h-[420px]', className)} />;

  if (boundary.length === 0) {
    return (
      <div className={className}>
        <EmptyState
          icon="grid"
          title="No plot boundary yet"
          description="Everything downstream — setbacks, FAR, the plans themselves — is measured against this outline. Start with the width × depth from the sale deed; corners can be dragged and edges retyped afterwards, and a DXF boundary can replace it any time."
          demoAction={{
            notApplicable:
              'The demo offer lives on the dashboard; inside a project the fastest start is typing the plot size below.',
          }}
        >
          <RectQuickStart className="mt-4 text-left" />
        </EmptyState>
      </div>
    );
  }

  const vp = makeViewport(boundary);
  const shown: Polygon =
    drag === null ? boundary : boundary.map((p, i) => (i === drag.vertexIndex ? drag.current : p));
  const shownCheck = checkBoundary(shown);
  const areaMm2 = polygonAreaMm2(shown);
  const front = frontEdgeIndex(plot.roads);

  const stroke = Math.max(vp.span / 260, 30);
  const handleR = Math.min(Math.max(vp.span / 55, 160), 480);
  const font = Math.min(Math.max(vp.span / 30, 280), 850);
  const step = gridStep(vp.span);

  // ── interaction handlers ─────────────────────────────────────────────────

  const fail = (text: string): void => setNotice({ tone: 'error', text });
  const ok = (): void => setNotice(null);

  const commitVertexMove = (vertexIndex: number, to: Pt): void => {
    const from = ringAt(boundary, vertexIndex);
    if (from.x === to.x && from.y === to.y) return;
    const result = moveVertex(boundary, vertexIndex, to);
    if (!result.ok) {
      fail(result.reason);
      return;
    }
    // Edge count is unchanged, so road indices survive as they are.
    const dispatched = actions.setBoundary(result.polygon, { label: 'Corner moved' });
    if (!dispatched.ok) fail(dispatched.issues[0]?.message ?? 'That corner move was not accepted.');
    else ok();
  };

  const onVertexPointerDown = (e: ReactPointerEvent<SVGCircleElement>, i: number): void => {
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    setSelectedVertex(i);
    setDrag({ vertexIndex: i, current: ringAt(boundary, i) });
  };

  const onVertexPointerMove = (e: ReactPointerEvent<SVGCircleElement>, i: number): void => {
    if (drag === null || drag.vertexIndex !== i || svgRef.current === null) return;
    const raw = clientToModel(vp, svgRef.current, e.clientX, e.clientY);
    const snap = e.shiftKey ? SNAP_FINE_MM : SNAP_COARSE_MM;
    setDrag({ vertexIndex: i, current: { x: snapMm(raw.x, snap), y: snapMm(raw.y, snap) } });
  };

  const onVertexPointerUp = (e: ReactPointerEvent<SVGCircleElement>, i: number): void => {
    if (drag === null || drag.vertexIndex !== i) return;
    e.currentTarget.releasePointerCapture(e.pointerId);
    const target = drag.current;
    setDrag(null);
    commitVertexMove(i, target);
  };

  const onVertexKeyDown = (e: ReactKeyboardEvent<SVGCircleElement>, i: number): void => {
    const stepMm = e.shiftKey ? SNAP_FINE_MM : SNAP_COARSE_MM;
    const v = ringAt(boundary, i);
    if (e.key === 'ArrowLeft') {
      e.preventDefault();
      commitVertexMove(i, { x: v.x - stepMm, y: v.y });
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      commitVertexMove(i, { x: v.x + stepMm, y: v.y });
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      commitVertexMove(i, { x: v.x, y: v.y + stepMm });
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      commitVertexMove(i, { x: v.x, y: v.y - stepMm });
    } else if (e.key === 'Delete' || e.key === 'Backspace') {
      e.preventDefault();
      removeCorner(i);
    } else if (e.key === 'Escape' && drag !== null) {
      e.preventDefault();
      setDrag(null);
    }
  };

  const removeCorner = (i: number): void => {
    const result = removeVertex(boundary, i);
    if (!result.ok) {
      fail(result.reason);
      return;
    }
    const nextRoads = remapRoadsAfterRemove(plot.roads, i, boundary.length);
    const dispatched = actions.setBoundary(result.polygon, {
      label: 'Corner removed',
      nextRoads,
    });
    if (!dispatched.ok) fail(dispatched.issues[0]?.message ?? 'That change was not accepted.');
    else {
      setSelectedVertex(null);
      ok();
    }
  };

  const addCorner = (edgeIndex: number): void => {
    const result = insertVertexOnEdge(boundary, edgeIndex);
    if (!result.ok) {
      fail(result.reason);
      return;
    }
    const nextRoads = remapRoadsAfterInsert(plot.roads, edgeIndex);
    const dispatched = actions.setBoundary(result.polygon, { label: 'Corner added', nextRoads });
    if (!dispatched.ok) fail(dispatched.issues[0]?.message ?? 'That change was not accepted.');
    else ok();
  };

  const beginEdgeEdit = (i: number): void => {
    setEditingEdge(i);
    setEdgeDraft(formatLength(edgeLengthMm(boundary, i), display));
  };

  const commitEdgeEdit = (i: number): void => {
    const raw = edgeDraft.trim();
    setEditingEdge(null);
    if (raw === '') return;
    const parsed = tryParseLengthMm(raw, display);
    if (!parsed.ok) {
      fail(`We couldn't read "${raw}" as a length. Try 40', 12.2m or 12200.`);
      return;
    }
    const result = setEdgeLengthMm(boundary, i, parsed.mm);
    if (!result.ok) {
      fail(result.reason);
      return;
    }
    const dispatched = actions.setBoundary(result.polygon, { label: 'Edge length' });
    if (!dispatched.ok) fail(dispatched.issues[0]?.message ?? 'That length was not accepted.');
    else ok();
  };

  // ── grid lines ───────────────────────────────────────────────────────────

  const gridLines: JSX.Element[] = [];
  {
    const x0 = Math.floor((vp.minX - vp.pad) / step) * step;
    const x1 = vp.minX - vp.pad + vp.vbW;
    const yTop = vp.maxY + vp.pad;
    const y1 = yTop - vp.vbH;
    for (let x = x0; x <= x1; x += step) {
      const s = toSvg(vp, { x, y: 0 }).x;
      gridLines.push(
        <line
          key={`gx${String(x)}`}
          x1={s}
          y1={0}
          x2={s}
          y2={vp.vbH}
          className="stroke-line"
          strokeOpacity={x === 0 ? 0.7 : 0.3}
          strokeWidth={stroke / 3}
        />,
      );
    }
    const yStart = Math.floor(y1 / step) * step;
    for (let y = yStart; y <= yTop; y += step) {
      const s = toSvg(vp, { x: 0, y }).y;
      gridLines.push(
        <line
          key={`gy${String(y)}`}
          x1={0}
          y1={s}
          x2={vp.vbW}
          y2={s}
          className="stroke-line"
          strokeOpacity={y === 0 ? 0.7 : 0.3}
          strokeWidth={stroke / 3}
        />,
      );
    }
  }

  const points = shown.map((p) => toSvg(vp, p));
  const pointsAttr = points.map((p) => `${String(p.x)},${String(p.y)}`).join(' ');

  return (
    <div className={cn('flex flex-col gap-2', className)}>
      {/* Header: live area readout + honest status (§15) */}
      <div className="flex flex-wrap items-center gap-2">
        <Chip severity="neutral" size="md" icon="ruler" className="garh-nums">
          {formatPlotArea(areaMm2, display)}
        </Chip>
        {selectedVertex !== null ? (
          <>
            <Chip severity="info" size="md" icon="pin">
              Corner {selectedVertex + 1} — arrows nudge (Shift = fine), Delete removes
            </Chip>
            <Button
              variant="ghost"
              size="sm"
              iconLeft="trash"
              onClick={() => removeCorner(selectedVertex)}
            >
              Remove corner
            </Button>
          </>
        ) : (
          <span className="text-2xs text-ink-subtle">
            Drag corners · click a length to type one · + on an edge adds a corner
          </span>
        )}
      </div>

      {notice === null ? null : (
        <div
          role="status"
          className={cn(
            'flex items-start justify-between gap-2 rounded-md border px-3 py-2 text-xs',
            notice.tone === 'error'
              ? 'border-fail-line bg-fail-soft text-fail-ink'
              : 'border-info-line bg-info-soft text-info-ink',
          )}
        >
          <span>{notice.text}</span>
          <button
            type="button"
            aria-label="Dismiss"
            onClick={() => setNotice(null)}
            className="garh-focus-ring rounded-sm font-semibold opacity-70 hover:opacity-100"
          >
            ×
          </button>
        </div>
      )}

      <div className="relative overflow-hidden rounded-lg border border-line bg-surface">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${String(vp.vbW)} ${String(vp.vbH)}`}
          className="block h-auto w-full touch-none select-none"
          role="application"
          aria-label="Plot boundary editor"
        >
          {gridLines}

          {/* Roads: honest width bands outside their edges */}
          {plot.roads.map((road) => {
            if (road.widthMm === null || road.edgeIndex >= boundary.length) return null;
            const a = ringAt(boundary, road.edgeIndex);
            const b = ringAt(boundary, road.edgeIndex + 1);
            const n = outwardNormal(boundary, road.edgeIndex);
            const off = road.widthMm / 2;
            const sa = toSvg(vp, {
              x: Math.round(a.x + n.x * off),
              y: Math.round(a.y + n.y * off),
            });
            const sb = toSvg(vp, {
              x: Math.round(b.x + n.x * off),
              y: Math.round(b.y + n.y * off),
            });
            const mid = {
              x: (sa.x + sb.x) / 2 + n.x * (off / 2),
              y: (sa.y + sb.y) / 2 - n.y * (off / 2),
            };
            return (
              <g key={`road${String(road.edgeIndex)}`}>
                <line
                  x1={sa.x}
                  y1={sa.y}
                  x2={sb.x}
                  y2={sb.y}
                  className="stroke-ink-subtle"
                  strokeOpacity={0.18}
                  strokeWidth={road.widthMm}
                />
                <text
                  x={mid.x}
                  y={mid.y}
                  textAnchor="middle"
                  fontSize={font * 0.8}
                  className="fill-ink-subtle select-none"
                >
                  {road.name ?? `${formatLength(road.widthMm, 'm')} road`}
                  {road.edgeIndex === front ? ' · entry' : ''}
                </text>
              </g>
            );
          })}

          {/* The boundary itself */}
          <polygon
            points={pointsAttr}
            className={cn(
              shownCheck.ok ? 'fill-brand' : 'fill-fail',
              shownCheck.ok ? 'stroke-brand' : 'stroke-fail',
            )}
            fillOpacity={0.08}
            strokeWidth={stroke}
            strokeLinejoin="round"
          />

          {/* Edge lengths (click-to-edit) and add-corner handles */}
          {shown.map((_, i) => {
            const a = ringAt(shown, i);
            const b = ringAt(shown, i + 1);
            const n = outwardNormal(shown, i);
            const midX = (a.x + b.x) / 2;
            const midY = (a.y + b.y) / 2;
            const label = toSvg(vp, {
              x: Math.round(midX + n.x * font * 1.2),
              y: Math.round(midY + n.y * font * 1.2),
            });
            const plus = toSvg(vp, {
              x: Math.round(midX - n.x * font * 1.1),
              y: Math.round(midY - n.y * font * 1.1),
            });
            const lengthText = formatLength(edgeLengthMm(shown, i), display);
            const facing = edgeFacing(shown, i, plot.northDeg);

            if (editingEdge === i) {
              const w = Math.max(font * 9, 3200);
              const h = font * 2;
              return (
                <foreignObject
                  key={`edit${String(i)}`}
                  x={label.x - w / 2}
                  y={label.y - h / 2}
                  width={w}
                  height={h}
                >
                  <input
                    autoFocus
                    value={edgeDraft}
                    aria-label={`Edge ${String(i + 1)} length`}
                    onChange={(e) => setEdgeDraft(e.target.value)}
                    onFocus={(e) => e.currentTarget.select()}
                    onBlur={() => commitEdgeEdit(i)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        commitEdgeEdit(i);
                      } else if (e.key === 'Escape') {
                        e.preventDefault();
                        setEditingEdge(null);
                      }
                    }}
                    style={{ fontSize: font * 0.9, width: '100%', height: '100%' }}
                    className="rounded-sm border border-brand bg-surface px-1 text-center text-ink garh-nums"
                  />
                </foreignObject>
              );
            }

            return (
              <g key={`edge${String(i)}`}>
                <text
                  x={label.x}
                  y={label.y}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  fontSize={font}
                  role="button"
                  tabIndex={0}
                  aria-label={`Edge ${String(i + 1)}, ${lengthText}${facing === null ? '' : `, faces ${facing}`}. Press Enter to type a new length.`}
                  onClick={() => beginEdgeEdit(i)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      beginEdgeEdit(i);
                    }
                  }}
                  className="garh-focus-ring cursor-pointer select-none fill-ink font-medium garh-nums"
                >
                  {lengthText}
                  {facing === null ? null : (
                    <tspan className="fill-ink-subtle" fontSize={font * 0.72}>
                      {'  '}
                      {facing}
                    </tspan>
                  )}
                </text>

                {/* Add-corner handle, inside the edge */}
                <g
                  role="button"
                  tabIndex={0}
                  aria-label={`Add a corner on edge ${String(i + 1)}`}
                  onClick={() => addCorner(i)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      addCorner(i);
                    }
                  }}
                  className="garh-focus-ring cursor-pointer opacity-40 hover:opacity-100"
                >
                  <circle
                    cx={plus.x}
                    cy={plus.y}
                    r={handleR * 0.7}
                    className="fill-surface stroke-line-strong"
                    strokeWidth={stroke / 2}
                  />
                  <line
                    x1={plus.x - handleR * 0.35}
                    y1={plus.y}
                    x2={plus.x + handleR * 0.35}
                    y2={plus.y}
                    className="stroke-ink"
                    strokeWidth={stroke / 2}
                  />
                  <line
                    x1={plus.x}
                    y1={plus.y - handleR * 0.35}
                    x2={plus.x}
                    y2={plus.y + handleR * 0.35}
                    className="stroke-ink"
                    strokeWidth={stroke / 2}
                  />
                </g>
              </g>
            );
          })}

          {/* Vertex handles last, so they win the hit test */}
          {points.map((sp, i) => (
            <circle
              key={`v${String(i)}`}
              cx={sp.x}
              cy={sp.y}
              r={handleR}
              role="button"
              tabIndex={0}
              aria-label={`Corner ${String(i + 1)} — drag or use arrow keys to move, Delete to remove`}
              onPointerDown={(e) => onVertexPointerDown(e, i)}
              onPointerMove={(e) => onVertexPointerMove(e, i)}
              onPointerUp={(e) => onVertexPointerUp(e, i)}
              onPointerCancel={() => setDrag(null)}
              onKeyDown={(e) => onVertexKeyDown(e, i)}
              onFocus={() => setSelectedVertex(i)}
              strokeWidth={stroke}
              className={cn(
                'garh-focus-ring cursor-grab touch-none active:cursor-grabbing',
                selectedVertex === i ? 'fill-brand stroke-brand' : 'fill-surface stroke-brand',
              )}
            />
          ))}
        </svg>

        {/* North compass, overlaid like a drawing's title-corner */}
        <div className="absolute right-2 top-2 rounded-lg border border-line bg-surface/90 p-2 shadow-sm">
          <NorthCompass size={72} />
        </div>
      </div>
    </div>
  );
}
