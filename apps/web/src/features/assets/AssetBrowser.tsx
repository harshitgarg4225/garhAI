/**
 * The asset browser, wired up: the catalogues, the signed-in user, the view.
 *
 * Everything interesting is elsewhere — this file exists so the integrator
 * mounts `<AssetBrowser />` with no props and gets a working panel, while
 * `AssetBrowserView` stays a pure component a test can drive with a real index.
 *
 * `bind` runs in an effect rather than at render because it reads
 * `localStorage`, and the store treats a re-bind of the same id as a no-op — so
 * this is safe to run on every render pass, including StrictMode's double
 * invocation.
 *
 * Binding `null` (a share-link viewer, or before sign-in) leaves the browser
 * fully usable and simply never writes: pins last for the session. That is the
 * same promise `features/layers` makes, and it is what keeps the panel working
 * in a browser with storage switched off.
 */

import { useEffect } from 'react';

import type { UnitsDisplay } from '../../lib/units';
import { useSessionStore } from '../../stores/session';
import { AssetBrowserView } from './AssetBrowserView';
import { useAssetBrowserStore } from './store';
import type { AssetRecord } from './types';
import { useAssetLibrary } from './useAssetLibrary';

export interface AssetBrowserProps {
  /**
   * Override the user the pins belong to. Omit and the signed-in user is used;
   * pass `null` to browse without persisting.
   */
  readonly userId?: string | null | undefined;
  readonly unitsDisplay?: UnitsDisplay | undefined;
  /** Arm the furniture tool, apply the material — the integrator's half. */
  readonly onUse?: ((record: AssetRecord) => void) | undefined;
  readonly className?: string | undefined;
}

export function AssetBrowser({
  userId,
  unitsDisplay,
  onUse,
  className,
}: AssetBrowserProps): JSX.Element {
  const library = useAssetLibrary();
  const sessionUserId = useSessionStore((s) => s.user?.id ?? null);
  const bind = useAssetBrowserStore((s) => s.bind);

  const effectiveUserId = userId === undefined ? sessionUserId : userId;

  useEffect(() => {
    bind(effectiveUserId);
  }, [bind, effectiveUserId]);

  return (
    <AssetBrowserView
      index={library.index}
      status={library.status}
      errorAction={library.errorAction}
      onReload={library.reload}
      unitsDisplay={unitsDisplay}
      onUse={onUse}
      className={className}
    />
  );
}
