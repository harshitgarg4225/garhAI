/**
 * NavModeHud.tsx — the 3D navigation controls: Orbit · Walk · Fit.
 *
 * DOM overlay (mounted OUTSIDE the `<Canvas>` by the page, floated over it).
 * §15: the walk hint is honest about v1 — collision is off, and the rig's
 * polar clamp means you look level-or-down (see `orbitOps.ts`'s header).
 */

import { Button, cn } from '@garh/ui';

import type { Nav3dApi } from './useNav3d';

export interface NavModeHudProps {
  nav: Nav3dApi;
  className?: string | undefined;
}

export function NavModeHud({ nav, className }: NavModeHudProps): JSX.Element {
  const walking = nav.navMode === 'walk';
  return (
    <div
      className={cn(
        'pointer-events-auto flex flex-col items-start gap-1 rounded-md border border-line bg-surface/95 p-1.5 shadow-sm',
        className,
      )}
    >
      <div className="flex items-center gap-1" role="group" aria-label="3D navigation mode">
        <Button
          size="sm"
          variant={walking ? 'ghost' : 'secondary'}
          aria-pressed={!walking}
          onClick={() => nav.setNavMode('orbit')}
        >
          Orbit
        </Button>
        <Button
          size="sm"
          variant={walking ? 'secondary' : 'ghost'}
          aria-pressed={walking}
          onClick={() => nav.setNavMode('walk')}
        >
          Walk
        </Button>
        <Button size="sm" variant="ghost" onClick={() => nav.fitToBuilding()}>
          Fit
        </Button>
      </div>
      <p className="max-w-56 px-1 text-2xs leading-4 text-ink-subtle">
        {walking
          ? 'WASD to move, drag to look, Shift to stride. Walk v1 has no collision — you will pass through walls — and looks level or down.'
          : 'Drag to orbit, scroll to zoom to your cursor, Shift-drag to pan. Double-click fits the building.'}
      </p>
    </div>
  );
}
