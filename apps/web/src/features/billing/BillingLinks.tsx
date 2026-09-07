/**
 * BillingLinks — the app shell's way into billing.
 *
 * "Billing" for every signed-in user. "Platform fee" only when the server's
 * `GET /admin/billing/markup` says this caller may set it — hiding the entry is a
 * courtesy to everyone else, not a gate: the PUT's 403 decides, and the page itself
 * renders read-only for a non-owner who types the URL.
 */

import type { JSX } from 'react';
import { Link } from 'react-router-dom';

import { api, type ApiClient } from '../../lib/api';
import { usePlatformFee } from './usePlatformFee';

const LINK_CLASS =
  'garh-focus-ring rounded-md px-2 py-1 text-sm text-ink-muted no-underline hover:bg-surface-muted hover:text-ink';

export interface BillingLinksProps {
  readonly client?: ApiClient | undefined;
}

export function BillingLinks({ client = api }: BillingLinksProps): JSX.Element {
  const fee = usePlatformFee(client);
  return (
    <nav aria-label="Billing" className="flex items-center gap-1">
      <Link to="/billing" className={LINK_CLASS}>
        Billing
      </Link>
      {fee.markup?.canSet === true ? (
        <Link to="/platform/fee" className={LINK_CLASS} data-testid="platform-fee-link">
          Platform fee
        </Link>
      ) : null}
    </nav>
  );
}
