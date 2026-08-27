/**
 * PresenceChips — who else has this project open, as initials in the top bar.
 *
 * Rayon-style ambient presence: no names in the chrome, just small colored
 * circles whose tooltip carries the name. The colour is derived from the
 * userId with a stable hash over the Badge tone pairs, so a teammate keeps
 * their colour across sessions and both themes get the token-audited
 * soft/ink contrast rather than an invented palette.
 *
 * At most {@link MAX_VISIBLE} chips render; the rest collapse into "+N". The
 * caller filters the signed-in user out — seeing your own avatar tells you
 * nothing.
 */

import { Tooltip, cn } from '@garh/ui';

export interface PresenceUser {
  readonly userId: string;
  readonly name: string;
}

export interface PresenceChipsProps {
  readonly users: readonly PresenceUser[];
  readonly className?: string | undefined;
}

const MAX_VISIBLE = 4;

/**
 * The fixed palette: the design system's five status soft/ink pairs plus
 * neutral. Every pair already passes the token contrast audit in both themes —
 * that is the whole reason to reuse them instead of hex values.
 */
const PALETTE = [
  'bg-brand-soft text-brand-ink',
  'bg-info-soft text-info-ink',
  'bg-pass-soft text-pass-ink',
  'bg-warn-soft text-warn-ink',
  'bg-fail-soft text-fail-ink',
  'bg-neutral-soft text-neutral-ink',
] as const;

/** FNV-1a over the userId — deterministic, so the colour survives a reload. */
export function presencePaletteIndex(userId: string, size: number = PALETTE.length): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < userId.length; i += 1) {
    hash ^= userId.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return Math.abs(hash) % size;
}

/** "Asha Rao" → "AR", "Priya" → "P", "" → "?". */
export function presenceInitials(name: string): string {
  const parts = name
    .trim()
    .split(/\s+/)
    .filter((part) => part !== '');
  const first = parts[0]?.[0] ?? '';
  const last = parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? '') : '';
  const text = `${first}${last}`.toUpperCase();
  return text === '' ? '?' : text;
}

export function PresenceChips({ users, className }: PresenceChipsProps): JSX.Element | null {
  if (users.length === 0) return null;

  const visible = users.slice(0, MAX_VISIBLE);
  const overflow = users.length - visible.length;
  const overflowNames = users
    .slice(MAX_VISIBLE)
    .map((user) => (user.name === '' ? 'A teammate' : user.name))
    .join(', ');

  return (
    <div
      role="group"
      aria-label={`${users.length} teammate${users.length === 1 ? '' : 's'} in this project now`}
      className={cn('flex items-center -space-x-1.5', className)}
    >
      {visible.map((user) => {
        const displayName = user.name === '' ? 'A teammate' : user.name;
        return (
          <Tooltip key={user.userId} delayMs={300} content={displayName}>
            <span
              aria-label={displayName}
              className={cn(
                'flex h-6 w-6 select-none items-center justify-center rounded-full',
                'ring-2 ring-surface text-2xs font-semibold',
                PALETTE[presencePaletteIndex(user.userId)],
              )}
            >
              {presenceInitials(user.name)}
            </span>
          </Tooltip>
        );
      })}
      {overflow > 0 ? (
        <Tooltip delayMs={300} content={overflowNames}>
          <span
            aria-label={`${overflow} more teammate${overflow === 1 ? '' : 's'}`}
            className={cn(
              'flex h-6 w-6 select-none items-center justify-center rounded-full',
              'bg-surface-muted ring-2 ring-surface text-2xs font-semibold text-ink-muted',
            )}
          >
            +{overflow}
          </span>
        </Tooltip>
      ) : null}
    </div>
  );
}
