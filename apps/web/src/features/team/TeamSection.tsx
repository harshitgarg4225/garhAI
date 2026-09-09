/**
 * TeamSection — who is in the practice, who has been asked, and the seats.
 *
 * Reads three things and shows them together: members (role, seat, last
 * sign-in), open invites (with resend / withdraw), and the plan's seats. Writes
 * are admin-only and the buttons say so rather than vanishing: a member should
 * still see who to ask.
 *
 * Every refusal the API can make is shown in its own words, because each is a
 * different next step: 409 `already_a_member` (they are already here), 409
 * `invite_pending` (resend instead), 402 `seat_limit_reached` (buy a seat — the
 * link goes to the Billing route by name), 429 `otp_rate_limited` on resend (the
 * server's Retry-After, said out loud), 409 `last_admin` (make someone else an
 * admin first).
 *
 * What this screen deliberately does NOT say: whether an invited address has an
 * account with some other practice. The API answers identically either way, and
 * a UI that inferred it from silence would be inventing a fact.
 */

import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { formatIndianDate } from '@garh/model';
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  ConfirmDialog,
  Field,
  Input,
  SelectField,
  copyToClipboard,
  useToast,
} from '@garh/ui';

import { ProblemPanel, toProblem } from '../../components';
import type { Problem } from '../../components';
import { api } from '../../lib/api';
import type { Invite, InviteInput, Member, Seats } from '../../lib/api';
import { AppError, ERROR_CODES } from '../../lib/errors';
import { selectIsAdmin, useSessionStore } from '../../stores/session';

interface TeamData {
  members: Member[];
  admins: number;
  invites: Invite[];
  seats: Seats | null;
}

const EMPTY_INVITE: InviteInput = { email: '', name: '', role: 'member', seatType: 'editor' };

export function describeLastSignIn(iso: string | null): string {
  return iso === null ? 'Never signed in' : `Last signed in ${formatIndianDate(iso)}`;
}

/** The message a refused invite deserves — by code, never by guessing. */
export function describeInviteRefusal(error: AppError): string {
  switch (error.code) {
    case ERROR_CODES.alreadyAMember:
      return 'That address is already a member of your practice.';
    case ERROR_CODES.invitePending:
      return 'That address already has an open invite — resend it from the list below.';
    case ERROR_CODES.seatLimitReached:
      return `${error.message} Invite them as a viewer, or add an editor seat on the Billing page.`;
    case ERROR_CODES.otpRateLimited:
    case ERROR_CODES.rateLimited:
      return `${error.message} Try again in ${error.retryAfterSeconds ?? 60} seconds.`;
    default:
      return error.message;
  }
}

export function TeamSection(): JSX.Element {
  const isAdmin = useSessionStore(selectIsAdmin);
  const me = useSessionStore((s) => s.user);
  const { toast } = useToast();

  const [data, setData] = useState<TeamData | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [form, setForm] = useState<InviteInput>(EMPTY_INVITE);
  const [formError, setFormError] = useState<string | undefined>(undefined);
  const [inviting, setInviting] = useState(false);
  const [lastLink, setLastLink] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [removing, setRemoving] = useState<Member | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const load = useCallback(async (): Promise<void> => {
    const [members, invites, seats] = await Promise.all([
      api.team.members(),
      api.team.invites(),
      api.billing.seats().catch(() => null),
    ]);
    setData({ members: members.items, admins: members.admins, invites, seats });
  }, []);

  useEffect(() => {
    let cancelled = false;
    setProblem(null);
    load().catch((err: unknown) => {
      if (!cancelled) setProblem(toProblem(err));
    });
    return () => {
      cancelled = true;
    };
  }, [load, reloadKey]);

  const sendInvite = async (): Promise<void> => {
    const email = form.email.trim().toLowerCase();
    if (form.name.trim().length < 2) {
      setFormError("Your colleague's name, so the seat has a name on it.");
      return;
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      setFormError('That does not look like an email address.');
      return;
    }
    setFormError(undefined);
    setInviting(true);
    try {
      const invite = await api.team.invite({ ...form, email, name: form.name.trim() });
      setLastLink(invite.url);
      setForm(EMPTY_INVITE);
      toast({
        severity: 'pass',
        title: `Invite sent to ${invite.email}`,
        description: 'They sign in with that address and land in your practice.',
      });
      await load();
    } catch (err) {
      setFormError(describeInviteRefusal(AppError.from(err)));
    } finally {
      setInviting(false);
    }
  };

  const resend = async (invite: Invite): Promise<void> => {
    setBusyId(invite.id);
    try {
      const fresh = await api.team.resendInvite(invite.id);
      setLastLink(fresh.url);
      toast({ severity: 'pass', title: `Invite re-sent to ${fresh.email}` });
      await load();
    } catch (err) {
      toast({
        severity: 'warn',
        title: "Couldn't resend",
        description: describeInviteRefusal(AppError.from(err)),
      });
    } finally {
      setBusyId(null);
    }
  };

  const withdraw = async (invite: Invite): Promise<void> => {
    setBusyId(invite.id);
    try {
      await api.team.revokeInvite(invite.id);
      toast({ severity: 'pass', title: `Invite to ${invite.email} withdrawn` });
      await load();
    } catch (err) {
      toast({ severity: 'warn', title: "Couldn't withdraw", description: toProblem(err).message });
    } finally {
      setBusyId(null);
    }
  };

  const changeRole = async (member: Member, role: 'admin' | 'member'): Promise<void> => {
    if (role === member.role) return;
    setBusyId(member.id);
    try {
      await api.team.setRole(member.id, role);
      toast({
        severity: 'pass',
        title: `${member.name} is now ${role === 'admin' ? 'an admin' : 'a member'}`,
        description: 'It reaches their session within fifteen minutes.',
      });
      await load();
    } catch (err) {
      const error = AppError.from(err);
      toast({
        severity: 'warn',
        title: "Couldn't change the role",
        description:
          error.code === ERROR_CODES.lastAdmin
            ? 'This is the only admin. Make someone else an admin first.'
            : error.message,
      });
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (member: Member): Promise<void> => {
    setBusyId(member.id);
    try {
      await api.team.removeMember(member.id);
      toast({
        severity: 'pass',
        title: `${member.name} removed`,
        description: 'Their seat is free again and every device they were signed in on is out.',
      });
      setRemoving(null);
      await load();
    } catch (err) {
      const error = AppError.from(err);
      toast({
        severity: 'warn',
        title: "Couldn't remove them",
        description:
          error.code === ERROR_CODES.lastAdmin
            ? 'This is the only admin. Make someone else an admin first.'
            : error.message,
      });
    } finally {
      setBusyId(null);
    }
  };

  if (problem !== null && data === null) {
    return <ProblemPanel problem={problem} onRetry={() => setReloadKey((k) => k + 1)} />;
  }
  if (data === null) {
    return (
      <p className="text-sm text-ink-muted" aria-busy="true">
        Loading your team…
      </p>
    );
  }

  const seats = data.seats;
  const openEditorInvites = data.invites.filter(
    (i) => i.seatType === 'editor' && i.status === 'pending',
  ).length;

  return (
    <div className="flex flex-col gap-6">
      {problem !== null ? (
        <ProblemPanel problem={problem} onRetry={() => setProblem(null)} />
      ) : null}

      {seats === null ? null : (
        <p className="text-sm text-ink-muted" data-testid="seat-summary">
          Editor seats: {seats.editorsUsed} of {seats.entitled} held
          {openEditorInvites > 0 ? `, ${openEditorInvites} promised to open invites` : ''}. Viewer
          seats are free.{' '}
          <Link to="/billing" className="text-brand-ink underline underline-offset-2">
            Manage seats on Billing
          </Link>
        </p>
      )}

      <Card>
        <CardHeader
          title="Members"
          description={`${data.members.length} in ${data.members.length === 1 ? 'the practice' : 'the practice'} · ${data.admins} admin${data.admins === 1 ? '' : 's'}`}
        />
        <CardBody>
          <ul className="divide-y divide-line" data-testid="member-list">
            {data.members.map((member) => {
              const isMe = member.id === me?.id;
              const lastAdmin = member.role === 'admin' && data.admins <= 1;
              return (
                <li
                  key={member.id}
                  className="flex flex-wrap items-center gap-3 py-3"
                  data-testid="member-row"
                  data-email={member.email}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium text-ink">{member.name}</span>
                      {isMe ? <Badge tone="info">You</Badge> : null}
                      {member.seat === null ? (
                        <Badge tone="neutral">No seat</Badge>
                      ) : (
                        <Badge tone="neutral">{member.seat.seatType} seat</Badge>
                      )}
                    </div>
                    <div className="truncate text-xs text-ink-muted">
                      {member.email} · {describeLastSignIn(member.lastSignInAt)}
                    </div>
                  </div>
                  {isAdmin ? (
                    <SelectField
                      label={`Role for ${member.name}`}
                      labelHidden
                      value={member.role}
                      onValueChange={(role) => void changeRole(member, role)}
                      disabled={busyId === member.id || lastAdmin}
                      options={[
                        { value: 'admin', label: 'Admin' },
                        { value: 'member', label: 'Member' },
                      ]}
                      fieldClassName="w-32"
                    />
                  ) : (
                    <Badge tone={member.role === 'admin' ? 'info' : 'neutral'}>
                      {member.role === 'admin' ? 'Admin' : 'Member'}
                    </Badge>
                  )}
                  {isAdmin ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      iconLeft="trash"
                      disabled={isMe || lastAdmin || busyId === member.id}
                      title={
                        isMe
                          ? 'Ask another admin to remove you.'
                          : lastAdmin
                            ? 'The only admin cannot be removed.'
                            : undefined
                      }
                      onClick={() => setRemoving(member)}
                    >
                      Remove
                    </Button>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Invite a colleague"
          description={
            isAdmin
              ? 'They get an email; signing in with that address puts them in your practice.'
              : 'Only an admin can invite. Ask one of them.'
          }
        />
        {isAdmin ? (
          <CardBody>
            <form
              className="flex flex-col gap-4"
              data-testid="invite-form"
              onSubmit={(e) => {
                e.preventDefault();
                void sendInvite();
              }}
            >
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Name" required>
                  {({ id, invalid }) => (
                    <Input
                      id={id}
                      placeholder="Rahul Verma"
                      value={form.name}
                      invalid={invalid}
                      onChange={(e) => setForm({ ...form, name: e.target.value })}
                    />
                  )}
                </Field>
                <Field label="Work email" required error={formError}>
                  {({ id, describedBy, invalid }) => (
                    <Input
                      id={id}
                      type="email"
                      inputMode="email"
                      iconLeft="mail"
                      placeholder="rahul@studio.in"
                      value={form.email}
                      aria-describedby={describedBy}
                      invalid={invalid}
                      onChange={(e) => {
                        setForm({ ...form, email: e.target.value });
                        if (formError !== undefined) setFormError(undefined);
                      }}
                    />
                  )}
                </Field>
                <SelectField
                  label="Role"
                  value={form.role}
                  onValueChange={(role) => setForm({ ...form, role })}
                  hint="Admins can invite, remove and change the practice."
                  options={[
                    { value: 'member', label: 'Member' },
                    { value: 'admin', label: 'Admin' },
                  ]}
                />
                <SelectField
                  label="Seat"
                  value={form.seatType}
                  onValueChange={(seatType) => setForm({ ...form, seatType })}
                  hint="Editors draw and count against the plan; viewers are free."
                  options={[
                    { value: 'editor', label: 'Editor' },
                    { value: 'viewer', label: 'Viewer' },
                  ]}
                />
              </div>
              <div className="flex items-center justify-between gap-3">
                {lastLink === null ? (
                  <span />
                ) : (
                  <button
                    type="button"
                    className="garh-focus-ring rounded-sm text-xs text-brand-ink underline underline-offset-2"
                    onClick={() => {
                      void copyToClipboard(lastLink).then(() =>
                        toast({ severity: 'pass', title: 'Invite link copied' }),
                      );
                    }}
                  >
                    Copy the invite link to paste on WhatsApp
                  </button>
                )}
                <Button type="submit" variant="primary" loading={inviting} loadingLabel="Sending">
                  Send invite
                </Button>
              </div>
            </form>
          </CardBody>
        ) : null}
      </Card>

      {data.invites.length === 0 ? null : (
        <Card>
          <CardHeader
            title="Open invites"
            description="Links last a week. Resend refreshes the link."
          />
          <CardBody>
            <ul className="divide-y divide-line" data-testid="invite-list">
              {data.invites.map((invite) => (
                <li
                  key={invite.id}
                  className="flex flex-wrap items-center gap-3 py-3"
                  data-testid="invite-row"
                  data-status={invite.status}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium text-ink">{invite.name}</span>
                      <Badge tone={invite.status === 'expired' ? 'warn' : 'info'}>
                        {invite.status === 'expired' ? 'Expired' : 'Pending'}
                      </Badge>
                      <Badge tone="neutral">
                        {invite.role} · {invite.seatType}
                      </Badge>
                    </div>
                    <div className="truncate text-xs text-ink-muted">
                      {invite.email} · invited by {invite.invitedByName ?? 'an admin'} · expires{' '}
                      {formatIndianDate(invite.expiresAt)}
                      {invite.sendCount > 1 ? ` · sent ${invite.sendCount} times` : ''}
                    </div>
                  </div>
                  {isAdmin ? (
                    <>
                      <Button
                        size="sm"
                        variant="secondary"
                        iconLeft="refresh"
                        disabled={busyId === invite.id}
                        onClick={() => void resend(invite)}
                      >
                        Resend
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        iconLeft="x"
                        disabled={busyId === invite.id}
                        onClick={() => void withdraw(invite)}
                      >
                        Withdraw
                      </Button>
                    </>
                  ) : null}
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      )}

      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open) setRemoving(null);
        }}
        title={removing === null ? 'Remove member' : `Remove ${removing.name}?`}
        description="They are signed out of every device now, their seat is released, and projects they were architect of record on keep the project but lose the name. The audit trail keeps everything they did."
        confirmLabel="Remove"
        destructive
        busy={removing !== null && busyId === removing.id}
        onConfirm={() => {
          if (removing !== null) void remove(removing);
        }}
      />
    </div>
  );
}
