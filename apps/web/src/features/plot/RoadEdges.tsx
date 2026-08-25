/**
 * RoadEdges — per-edge road toggle + width, dispatching `plot.set_road`.
 *
 * The FRONT edge matters: the rules engine calls the edge with the widest
 * road "front" (ties → lowest index), the setback and FAR tables band on that
 * road's width, and the solver puts the entry there. This panel says which
 * edge won rather than leaving the architect to guess.
 */

import { formatLength } from '@garh/model';
import { Chip, Input, LengthInput, PanelSection, SkeletonText, Tooltip, cn } from '@garh/ui';

import { edgeFacing, edgeLengthMm, frontEdgeIndex } from './geometry';
import { useModelReady, usePlotActions, usePlotDoc, useUnitsDisplay } from './usePlot';

/**
 * Default when an edge is first marked as having a road. 9 m exactly: every
 * seeded pack bands at 9 m, and 9.0 lands in the ≥9 m band — a typical layout
 * road, not a thumb on the scales. It is editable right next to the toggle.
 */
const DEFAULT_ROAD_WIDTH_MM = 9000;

export interface RoadEdgesProps {
  className?: string | undefined;
}

export function RoadEdges({ className }: RoadEdgesProps): JSX.Element {
  const ready = useModelReady();
  const plot = usePlotDoc();
  const display = useUnitsDisplay();
  const actions = usePlotActions();

  if (!ready) {
    return (
      <PanelSection title="Roads" className={className ?? ''}>
        <SkeletonText lines={3} />
      </PanelSection>
    );
  }

  const boundary = plot.boundary;
  if (boundary.length < 3) {
    return (
      <PanelSection title="Roads" className={className ?? ''}>
        <p className="text-xs text-ink-muted">
          Draw the plot boundary first — roads attach to its edges, and the front road&apos;s width
          drives the setback and FAR tables.
        </p>
      </PanelSection>
    );
  }

  const front = frontEdgeIndex(plot.roads);
  const roadByEdge = new Map(plot.roads.map((r) => [r.edgeIndex, r]));

  return (
    <PanelSection title="Roads" className={className ?? ''}>
      <p className="mb-2 text-2xs leading-4 text-ink-subtle">
        Mark every edge that touches a road. The widest one becomes the entry (front) edge — the
        bye-law tables band on its width.
      </p>
      <ul className="space-y-2">
        {boundary.map((_, i) => {
          const road = roadByEdge.get(i) ?? null;
          const widthMm = road === null ? null : road.widthMm;
          const roadName = road === null ? null : road.name;
          const facing = edgeFacing(boundary, i, plot.northDeg);
          return (
            <li
              key={i}
              className={cn(
                'rounded-md border px-2.5 py-2',
                widthMm !== null ? 'border-line-strong bg-surface' : 'border-line bg-surface-muted',
              )}
            >
              <div className="flex items-center gap-2">
                <label className="flex flex-1 cursor-pointer items-center gap-2 text-xs text-ink">
                  <input
                    type="checkbox"
                    checked={widthMm !== null}
                    onChange={(e) => {
                      if (e.target.checked) actions.setRoad(i, DEFAULT_ROAD_WIDTH_MM, roadName);
                      else actions.setRoad(i, null, null);
                    }}
                    className="garh-focus-ring h-3.5 w-3.5 rounded-sm border-line-strong"
                  />
                  <span className="font-medium">Edge {i + 1}</span>
                  <span className="text-ink-subtle garh-nums">
                    {formatLength(edgeLengthMm(boundary, i), display)}
                    {facing === null ? '' : ` · faces ${facing}`}
                  </span>
                </label>
                {i === front ? (
                  <Tooltip content="The widest road wins; the solver places the entry here and the setback tables read this width.">
                    <Chip severity="info" size="sm" icon="home">
                      Front · entry
                    </Chip>
                  </Tooltip>
                ) : null}
              </div>

              {widthMm === null ? null : (
                <div className="mt-2 flex flex-wrap items-end gap-2 pl-5">
                  <LengthInput
                    label={`Road width on edge ${String(i + 1)}`}
                    labelHidden
                    valueMm={widthMm}
                    onCommitMm={(mm) => actions.setRoad(i, mm, roadName)}
                    display={display}
                    bareUnit="m"
                    minMm={1000}
                    maxMm={60_000}
                    hideMmHint
                    placeholder="9m"
                    className="w-28"
                  />
                  <div className="w-40">
                    {/* Keyed remount so an undo that changes the name is not
                        swallowed by the uncontrolled input's stale defaultValue. */}
                    <Input
                      key={`name-${String(i)}-${roadName ?? ''}`}
                      aria-label={`Road name for edge ${String(i + 1)}`}
                      placeholder="Road name (site plan)"
                      defaultValue={roadName ?? ''}
                      onBlur={(e) => {
                        const name = e.target.value.trim();
                        if ((roadName ?? '') !== name) {
                          actions.setRoad(i, widthMm, name === '' ? null : name);
                        }
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') e.currentTarget.blur();
                      }}
                    />
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </PanelSection>
  );
}
