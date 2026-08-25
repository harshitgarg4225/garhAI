/**
 * BriefPage — the Phase 2 surface: plot first, then the brief (F1 + F2).
 *
 * The order is deliberate. Everything downstream — setbacks, FAR, the plans
 * themselves — is measured against the boundary, so the plot editor sits at
 * the top and the brief below it. One page rather than two: an architect
 * setting up a project does both in one sitting, and the §15 "generated plans
 * for a real plot in under 10 minutes" clock does not want a tab switch in
 * the middle.
 *
 * This page COMPOSES; it does not own logic. Every control on it comes from
 * `features/plot` and `features/brief`, all of which read the model store and
 * write exclusively via op dispatch (golden rule 1). What the page owns:
 *
 *   - the layout and section anchors (the completeness meter's jump targets),
 *   - the DXF import dialog trigger (the flow itself is `DxfImportDialog`),
 *   - the teaching copy that ties the two halves together.
 *
 * Empty states: the plot editor teaches "draw or import" (its own empty state
 * plus the always-visible Import DXF button up top); the brief side's meter at
 * 0% teaches "start with bedrooms and a budget, or paste the client's words".
 */

import { useCallback, useRef, useState } from 'react';
import { Button, Card } from '@garh/ui';
import { PageBody } from '../../components';
import {
  AreaReadout,
  PlotEditor,
  RegProfilePanel,
  RoadEdges,
} from '../../features/plot';
import {
  BriefForm,
  CompletenessMeter,
  FreeTextParse,
  VastuSelector,
} from '../../features/brief';
import { useProjectOutlet } from '../ProjectShell';
import { DxfImportDialog } from './DxfImportDialog';

export function BriefPage(): JSX.Element {
  const { project } = useProjectOutlet();
  const [importOpen, setImportOpen] = useState(false);

  // The completeness meter lists what is missing; clicking an item scrolls to
  // the section that answers it. Vastu has its own card, everything else is
  // the form.
  const formRef = useRef<HTMLElement>(null);
  const vastuRef = useRef<HTMLElement>(null);
  const jumpTo = useCallback((itemId: string): void => {
    const target = itemId === 'vastu' ? vastuRef.current : formRef.current;
    target?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

  return (
    <PageBody className="max-w-6xl">
      {/* ── The plot (F1) ─────────────────────────────────────────────── */}
      <section aria-labelledby="plot-heading">
        <div className="flex flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <h2 id="plot-heading" className="text-base font-semibold text-ink">
              Plot
            </h2>
            <p className="mt-0.5 text-xs leading-5 text-ink-muted">
              Draw the boundary below — or import it from a DXF your surveyor or CAD tool
              exported. Corners drag, edge lengths are click-to-type, and every change is one
              undo step.
            </p>
          </div>
          <AreaReadout />
          <Button
            variant="secondary"
            size="sm"
            iconLeft="download"
            onClick={() => setImportOpen(true)}
          >
            Import DXF
          </Button>
        </div>

        <div className="mt-3 grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
          <PlotEditor className="min-w-0" />
          {/* PanelSections stack with their own dividers — one Card, no extra
              padding, exactly how the §12 inspector composes them. */}
          <Card className="min-w-0 self-start">
            <RoadEdges />
            <RegProfilePanel />
          </Card>
        </div>
      </section>

      {/* ── The brief (F2) ────────────────────────────────────────────── */}
      <section aria-labelledby="brief-heading" className="mt-8">
        <h2 id="brief-heading" className="text-base font-semibold text-ink">
          Brief
        </h2>
        <p className="mt-0.5 text-xs leading-5 text-ink-muted">
          Describe the house in the form, or paste the client&rsquo;s own words and review what we
          read out of them. Every AI assumption is an editable chip — nothing applies silently.
        </p>

        <Card className="mt-3 p-4">
          <CompletenessMeter onJumpTo={jumpTo} />
        </Card>

        <FreeTextParse projectId={project.id} className="mt-4" />

        <section ref={formRef} aria-label="Brief form" className="mt-4 scroll-mt-4">
          <BriefForm />
        </section>

        <section ref={vastuRef} aria-label="Vastu" className="mt-4 scroll-mt-4">
          <VastuSelector />
        </section>
      </section>

      <DxfImportDialog open={importOpen} onOpenChange={setImportOpen} projectId={project.id} />
    </PageBody>
  );
}

export default BriefPage;
