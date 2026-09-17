/**
 * PlanEnvelopeBanner — "you changed the plot after a plan was applied".
 *
 * A boundary or road edit moves the setbacks and the front edge but never
 * touches the house, so an applied plan can be standing outside its envelope
 * with nothing on the Plot tab saying so. This banner says so — non-blocking
 * (golden rule 5: compliance informs, never forbids), dismissable, and with
 * the one certain number this side can compute without a rule pack: how many
 * walls now end outside the boundary. The setbacks themselves are the
 * compliance report's call, hence the link.
 */

import { Link } from 'react-router-dom';

import { Button, Icon } from '@garh/ui';

import { wallsOutsideBoundary } from './geometry';
import { useHouseWalls, usePlotDoc, usePlotEditSession } from './usePlot';

export interface PlanEnvelopeBannerProps {
  /** Route of the compliance tab, e.g. `/projects/<id>/compliance`. */
  complianceHref?: string | undefined;
  className?: string | undefined;
}

export function PlanEnvelopeBanner({
  complianceHref,
  className,
}: PlanEnvelopeBannerProps): JSX.Element | null {
  const touchedAt = usePlotEditSession((s) => s.planTouchedAt);
  const dismiss = usePlotEditSession((s) => s.dismiss);
  const walls = useHouseWalls();
  const plot = usePlotDoc();

  if (touchedAt === null || walls.length === 0) return null;
  const outside = wallsOutsideBoundary(plot.boundary, walls);

  return (
    <div
      role="status"
      data-testid="plan-envelope-banner"
      className={
        'flex items-start justify-between gap-3 rounded-md border border-warn-line bg-warn-soft px-3 py-2 text-xs text-warn-ink ' +
        (className ?? '')
      }
    >
      <div className="flex items-start gap-2">
        <Icon name="alert-triangle" size={14} className="mt-0.5 shrink-0" />
        <p>
          The plot changed after a plan was applied — the plan may now sit outside its setbacks
          {outside > 0
            ? `, and ${String(outside)} wall${outside === 1 ? '' : 's'} end${outside === 1 ? 's' : ''} outside the boundary`
            : ''}
          .{' '}
          {complianceHref === undefined ? (
            'Re-check compliance before relying on it.'
          ) : (
            <Link to={complianceHref} className="font-medium underline underline-offset-2">
              Re-check compliance
            </Link>
          )}
        </p>
      </div>
      <Button variant="ghost" size="sm" onClick={dismiss} aria-label="Dismiss plan warning">
        Dismiss
      </Button>
    </div>
  );
}
