/**
 * The audit trail as a person reads it.
 *
 * `audit_log.action` is `<entity>.<verb>` written by a dozen call sites, and this
 * screen is the first thing that renders it to a human. Two rules:
 *
 *   - **describe, never invent.** An action this file has no phrasing for is shown
 *     as its own code, not as a guess — a trail that renames what it does not
 *     recognise is a trail nobody can testify from. `ACTION_PHRASES` covers what
 *     the server's vocabulary endpoint actually returns today; anything new reads
 *     as `firm.renamed` until someone writes its sentence.
 *   - **meta is already redacted server-side** (`routers/privacy.py`). What arrives
 *     is rendered as-is; this file adds no second opinion about what is a secret.
 */

import type { AuditEntry } from '../../lib/api';

/** `<entity>.<verb>` → the sentence an architect reads. */
export const ACTION_PHRASES: Readonly<Record<string, string>> = {
  'auth.signup': 'signed up',
  'auth.login': 'signed in',
  'auth.logout': 'signed out',
  'auth.refresh_reuse': 'a refresh token was reused — the session family was revoked',
  'two_factor.enabled': 'turned two-factor sign-in on',
  'two_factor.disabled': 'turned two-factor sign-in off',
  'two_factor.recovery_regenerated': 'replaced their recovery codes',
  'user.invited': 'invited a colleague',
  'user.role_changed': 'changed a colleague’s role',
  'user.removed': 'removed an account',
  'firm.updated': 'edited the practice',
  'project.created': 'created a project',
  'project.deleted': 'deleted a project',
  'project.archived': 'archived a project',
  'share_link.created': 'created a share link',
  'share_link.revoked': 'revoked a share link',
  'export.created': 'exported drawings',
  'privacy.data_exported': 'downloaded their own data (DPDP §11)',
  'seed.completed': 'seeded the demo data',
  'billing.plan_changed': 'changed the plan',
  'billing.invoice_issued': 'issued an invoice',
  'compliance.override_created': 'recorded a compliance override',
  'compliance.override_revoked': 'revoked a compliance override',
};

/** The sentence for one row, or the raw action when there is no phrasing yet. */
export function describeAction(action: string): string {
  return ACTION_PHRASES[action] ?? action;
}

/** "Asha Rao" · "Someone (erased)" · "The system". */
export function describeActor(entry: AuditEntry): string {
  if (entry.actorName !== null && entry.actorName !== '') return entry.actorName;
  if (entry.actorId !== null) return 'Someone (account erased)';
  return 'The system';
}

/** `2026-09-20, 14:05` in the Indian convention the rest of the product uses. */
export function formatWhen(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  return at.toLocaleString('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** The entity a row is about, for the second column. `—` when it names none. */
export function describeEntity(entry: AuditEntry): string {
  if (entry.entity === '') return '—';
  const id = entry.entityId;
  return id === null || id === '' ? entry.entity : `${entry.entity} ${id.slice(0, 8)}`;
}

/**
 * `meta` as `key: value` pairs, flattened one level, with objects and arrays left
 * as compact JSON. `[redacted]` arrives from the server and is passed through —
 * the reader should see that something was withheld.
 */
export function metaPairs(
  meta: Readonly<Record<string, unknown>>,
): { key: string; value: string }[] {
  return Object.entries(meta).map(([key, value]) => ({
    key,
    value:
      value === null || value === undefined
        ? '—'
        : typeof value === 'object'
          ? JSON.stringify(value)
          : String(value),
  }));
}

/** Group the trail by calendar day, newest first, for the day headings. */
export function groupByDay(entries: readonly AuditEntry[]): { day: string; items: AuditEntry[] }[] {
  const groups: { day: string; items: AuditEntry[] }[] = [];
  for (const entry of entries) {
    const at = new Date(entry.at);
    const day = Number.isNaN(at.getTime())
      ? 'Unknown date'
      : at.toLocaleDateString('en-IN', { day: '2-digit', month: 'long', year: 'numeric' });
    const last = groups[groups.length - 1];
    if (last !== undefined && last.day === day) last.items.push(entry);
    else groups.push({ day, items: [entry] });
  }
  return groups;
}
