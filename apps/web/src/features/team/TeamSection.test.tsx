/**
 * The Team page against a mocked API: what an admin sees, what they can send, and
 * how each refusal reads. The UI kit is real; the store and the client are not.
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { ToastProvider } from '@garh/ui';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppError, ERROR_CODES } from '../../lib/errors';

const { team, billing, sessionState } = vi.hoisted(() => ({
  team: {
    members: vi.fn(),
    invites: vi.fn(),
    invite: vi.fn(),
    resendInvite: vi.fn(),
    revokeInvite: vi.fn(),
    setRole: vi.fn(),
    removeMember: vi.fn(),
  },
  billing: { seats: vi.fn() },
  sessionState: {
    user: {
      id: 'u1',
      email: 'asha@studio.in',
      name: 'Asha Rao',
      role: 'admin' as string,
      coaNumber: null as string | null,
    },
  },
}));

vi.mock('../../lib/api', () => ({ api: { team, billing } }));
vi.mock('../../stores/session', () => ({
  useSessionStore: (selector: (s: typeof sessionState) => unknown) => selector(sessionState),
  selectIsAdmin: (s: typeof sessionState) => s.user.role === 'admin',
}));
vi.mock('react-router-dom', () => ({
  Link: ({ to, children, ...rest }: { to: string; children: React.ReactNode }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

import { TeamSection, describeInviteRefusal, describeLastSignIn } from './TeamSection';

const MEMBERS = {
  items: [
    {
      id: 'u1',
      email: 'asha@studio.in',
      name: 'Asha Rao',
      role: 'admin' as const,
      coaNumber: null,
      seat: null,
      lastSignInAt: null,
      createdAt: '2026-09-01T00:00:00Z',
    },
    {
      id: 'u2',
      email: 'rahul@studio.in',
      name: 'Rahul Verma',
      role: 'member' as const,
      coaNumber: null,
      seat: { id: 's1', seatType: 'editor' },
      lastSignInAt: '2026-09-08T10:00:00Z',
      createdAt: '2026-09-02T00:00:00Z',
    },
  ],
  count: 2,
  admins: 1,
};

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  for (const fn of Object.values(team)) fn.mockReset();
  billing.seats.mockReset();
  team.members.mockResolvedValue(MEMBERS);
  team.invites.mockResolvedValue([]);
  billing.seats.mockResolvedValue({
    entitled: 1,
    editorsUsed: 1,
    viewersUsed: 0,
    available: 0,
    seats: [],
  });
  sessionState.user.role = 'admin';
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function render(): Promise<void> {
  act(() =>
    root.render(
      <ToastProvider>
        <TeamSection />
      </ToastProvider>,
    ),
  );
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function input(label: string): HTMLInputElement {
  const el = Array.from(container.querySelectorAll('label')).find((l) =>
    (l.textContent ?? '').startsWith(label),
  );
  if (el === undefined) throw new Error(`no label ${label}`);
  const target = document.getElementById(el.htmlFor) as HTMLInputElement | null;
  if (target === null) throw new Error(`no control for ${label}`);
  return target;
}

function type(el: HTMLInputElement, value: string): void {
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  act(() => {
    setter?.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

async function submitInvite(): Promise<void> {
  const form = container.querySelector<HTMLFormElement>('[data-testid="invite-form"]');
  if (form === null) throw new Error('no invite form');
  await act(async () => {
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe('TeamSection', () => {
  it('lists members with role, seat and last sign-in, and the seats against the plan', async () => {
    await render();
    const rows = container.querySelectorAll('[data-testid="member-row"]');
    expect(rows).toHaveLength(2);
    expect(rows[0]?.textContent).toContain('Never signed in');
    expect(rows[0]?.textContent).toContain('No seat');
    expect(rows[1]?.textContent).toContain('editor seat');
    expect(rows[1]?.textContent).toContain('Last signed in');
    expect(container.querySelector('[data-testid="seat-summary"]')?.textContent).toContain(
      'Editor seats: 1 of 1 held',
    );
    expect(container.querySelector('a[href="/billing"]')).not.toBeNull();
  });

  it('sends an invite with the role and seat chosen, then offers the link', async () => {
    team.invite.mockResolvedValue({
      id: 'i1',
      email: 'meera@studio.in',
      name: 'Meera Iyer',
      role: 'member',
      seatType: 'viewer',
      status: 'pending',
      invitedBy: 'u1',
      invitedByName: 'Asha Rao',
      expiresAt: '2026-09-16T00:00:00Z',
      lastSentAt: '2026-09-09T00:00:00Z',
      sendCount: 1,
      createdAt: '2026-09-09T00:00:00Z',
      url: 'http://localhost:5173/login?invite=tok',
    });
    await render();
    type(input('Name'), 'Meera Iyer');
    type(input('Work email'), 'Meera@Studio.in');
    const seatLabel = Array.from(container.querySelectorAll('label')).find(
      (l) => (l.textContent ?? '').trim() === 'Seat',
    );
    if (seatLabel === undefined) throw new Error('no seat label');
    const seat = document.getElementById(seatLabel.htmlFor) as HTMLSelectElement | null;
    if (seat === null) throw new Error('no seat select');
    act(() => {
      // eslint-disable-next-line @typescript-eslint/unbound-method
      const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
      setter?.call(seat, 'viewer');
      seat.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(seat.value).toBe('viewer');
    await submitInvite();
    expect(team.invite).toHaveBeenCalledWith({
      email: 'meera@studio.in',
      name: 'Meera Iyer',
      role: 'member',
      seatType: 'viewer',
    });
    expect(container.textContent).toContain('Copy the invite link');
  });

  it('shows the seat gate in the form with the Billing next step', async () => {
    team.invite.mockRejectedValue(
      new AppError({
        code: ERROR_CODES.seatLimitReached,
        message: 'Your Free plan includes 1 editor seat(s); 1 are held.',
        action: 'Release a seat.',
        status: 402,
      }),
    );
    await render();
    type(input('Name'), 'Meera Iyer');
    type(input('Work email'), 'meera@studio.in');
    await submitInvite();
    expect(container.textContent).toContain('Your Free plan includes 1 editor seat');
    expect(container.textContent).toContain('Billing page');
  });

  it('shows open invites with resend and withdraw', async () => {
    team.invites.mockResolvedValue([
      {
        id: 'i1',
        email: 'meera@studio.in',
        name: 'Meera Iyer',
        role: 'admin',
        seatType: 'editor',
        status: 'expired',
        invitedBy: 'u1',
        invitedByName: 'Asha Rao',
        expiresAt: '2026-09-01T00:00:00Z',
        lastSentAt: '2026-08-25T00:00:00Z',
        sendCount: 2,
        createdAt: '2026-08-25T00:00:00Z',
        url: null,
      },
    ]);
    await render();
    const row = container.querySelector('[data-testid="invite-row"]');
    expect(row?.getAttribute('data-status')).toBe('expired');
    expect(row?.textContent).toContain('Expired');
    expect(row?.textContent).toContain('sent 2 times');
    expect(row?.textContent).toContain('Resend');
    expect(row?.textContent).toContain('Withdraw');
  });

  it('hides every admin control from a member but still shows the team', async () => {
    sessionState.user.role = 'member';
    await render();
    expect(container.querySelectorAll('[data-testid="member-row"]')).toHaveLength(2);
    expect(container.querySelector('[data-testid="invite-form"]')).toBeNull();
    expect(container.textContent).toContain('Only an admin can invite');
    expect(container.querySelector('select')).toBeNull();
  });
});

describe('the words', () => {
  it('describes a refusal by its code, and never guesses about other practices', () => {
    const mk = (code: string, retry: number | null = null): AppError =>
      new AppError({ code, message: 'Nope.', action: 'x', status: 409, retryAfterSeconds: retry });
    expect(describeInviteRefusal(mk(ERROR_CODES.alreadyAMember))).toContain('already a member');
    expect(describeInviteRefusal(mk(ERROR_CODES.invitePending))).toContain('resend');
    expect(describeInviteRefusal(mk(ERROR_CODES.otpRateLimited, 17))).toContain('17 seconds');
    expect(describeInviteRefusal(mk('anything_else'))).toBe('Nope.');
  });

  it('says "never" rather than a blank for a member who has not turned up', () => {
    expect(describeLastSignIn(null)).toBe('Never signed in');
    expect(describeLastSignIn('2026-09-08T10:00:00Z')).toMatch(/^Last signed in /);
  });
});
