/**
 * ProjectCard — one tile on the dashboard.
 *
 * Indian formatting throughout (§15): plot area reads "1,200.0 sq ft · 133 gaj"
 * via `formatPlotArea`, and the date is DD-MM-YYYY via `formatIndianDate`. Both
 * come from @garh/model so the dashboard, the drawings and the API agree.
 *
 * The whole card navigates (one tab stop, real anchor). Row actions live in a
 * separate control OUTSIDE the anchor — nesting a button inside a link is the
 * classic way to make a card unusable with a keyboard.
 */

import type { ReactNode } from 'react';
import { formatIndianDate, formatPlotArea } from '@garh/model';
import { Badge, CardLink, Icon, IconButton, Skeleton } from '@garh/ui';
import { ProjectStageChips } from './ProjectStageChips';
import type { ProjectStage, ProjectSummaryVM } from './types';

export interface ProjectCardProps {
  project: ProjectSummaryVM;
  /** Where the card navigates. Pages build this from the router. */
  href: string;
  /**
   * Render the anchor. The dashboard passes react-router's <Link> so the
   * navigation stays client-side; without it this is a plain <a>.
   */
  renderLink?:
    | ((props: { href: string; className: string; children: ReactNode }) => ReactNode)
    | undefined;
  onStageClick?: ((stage: ProjectStage) => void) | undefined;
  /** Overflow menu trigger. Omit for the demo project. */
  onMore?: (() => void) | undefined;
}

/**
 * NOTE: `group` belongs on the positioned WRAPPER below, not here. The overflow
 * menu is a sibling of the anchor (nesting a button inside a link breaks
 * keyboard use), and `group-hover:` only reaches descendants of the element
 * carrying `group` — with it on the anchor the menu button was reachable by
 * keyboard (`focus-within`) but never appeared on hover.
 */
const CARD_CLASS = 'flex flex-col overflow-hidden';

export function ProjectCard({
  project,
  href,
  renderLink,
  onStageClick,
  onMore,
}: ProjectCardProps): JSX.Element {
  const meta = [project.cityLabel, project.configuration].filter(
    (x): x is string => x !== undefined && x !== '',
  );

  const inner = (
    <>
      <div className="relative aspect-[16/9] w-full overflow-hidden border-b border-line bg-surface-sunken">
        {project.thumbnailUrl === undefined ? (
          <div className="flex h-full flex-col items-center justify-center gap-1.5 text-ink-subtle">
            <Icon name="home" size={22} />
            <span className="text-2xs">No plan yet</span>
          </div>
        ) : (
          <img
            src={project.thumbnailUrl}
            alt=""
            loading="lazy"
            className="h-full w-full object-cover"
          />
        )}
        {project.isDemo ? (
          <span className="absolute left-2 top-2">
            <Badge tone="brand">Demo</Badge>
          </span>
        ) : null}
        {project.status === 'archived' ? (
          <span className="absolute right-2 top-2">
            <Badge tone="neutral">Archived</Badge>
          </span>
        ) : null}
      </div>

      <div className="flex flex-1 flex-col gap-2 p-3.5">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold text-ink">{project.name}</h3>
          {project.clientName === undefined ? null : (
            <p className="truncate text-xs text-ink-muted">{project.clientName}</p>
          )}
        </div>

        <dl className="flex flex-col gap-0.5 text-xs text-ink-muted">
          <div className="flex items-center gap-1.5">
            <dt className="sr-only">Plot area</dt>
            <dd className="garh-nums">
              {project.plotAreaMm2 === null
                ? 'Plot not set'
                : formatPlotArea(project.plotAreaMm2, project.unitsDisplay)}
            </dd>
          </div>
          {meta.length === 0 ? null : (
            <div>
              <dt className="sr-only">Details</dt>
              <dd>{meta.join(' · ')}</dd>
            </div>
          )}
        </dl>

        <ProjectStageChips stages={project.stages} onStageClick={onStageClick} className="mt-auto pt-1" />

        <p className="text-2xs text-ink-subtle garh-nums">
          Updated {formatIndianDate(project.updatedAt)}
        </p>
      </div>
    </>
  );

  const linkNode =
    renderLink !== undefined ? (
      renderLink({
        href,
        className: `${CARD_CLASS} rounded-lg border border-line bg-surface garh-focus-ring text-left no-underline transition-shadow hover:border-line-strong hover:shadow-md`,
        children: inner,
      })
    ) : (
      <CardLink href={href} className={CARD_CLASS}>
        {inner}
      </CardLink>
    );

  if (onMore === undefined) return <div className="relative">{linkNode}</div>;

  return (
    <div className="group relative">
      {linkNode}
      <div className="absolute right-2 top-2 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
        <IconButton
          label={`More actions for ${project.name}`}
          icon="more-horizontal"
          size="sm"
          variant="secondary"
          onClick={onMore}
        />
      </div>
    </div>
  );
}

/** The loading tile. Same geometry as the real card so nothing jumps. */
export function ProjectCardSkeleton(): JSX.Element {
  return (
    <div className="flex flex-col overflow-hidden rounded-lg border border-line bg-surface">
      <Skeleton className="aspect-[16/9] w-full" shape="block" />
      <div className="flex flex-col gap-2 p-3.5">
        <Skeleton className="h-4 w-3/5" />
        <Skeleton className="h-3 w-2/5" />
        <div className="mt-1 flex gap-1.5">
          <Skeleton className="h-5 w-14" shape="block" />
          <Skeleton className="h-5 w-16" shape="block" />
          <Skeleton className="h-5 w-14" shape="block" />
          <Skeleton className="h-5 w-20" shape="block" />
        </div>
        <Skeleton className="h-2.5 w-24" />
      </div>
    </div>
  );
}
