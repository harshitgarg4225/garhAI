/**
 * RendersPage — AI renders (F6, playbook Phase 7). The phase placeholder is
 * gone: this tab now IS the render surface.
 *
 * Everything lives in `features/renders` (capture, jobs, history, packs,
 * WhatsApp share); this page is just the mount point the router already knew
 * about. Capture itself happens on the 3D view — this tab has no canvas, so
 * its "New render" buttons hand a pending request to the launcher over there
 * (see `features/renders/store.ts` for the contract).
 */

import { RendersTab } from '../../features/renders';

export function RendersPage(): JSX.Element {
  return <RendersTab />;
}

export default RendersPage;
