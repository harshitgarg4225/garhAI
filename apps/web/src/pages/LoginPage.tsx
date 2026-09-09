/**
 * LoginPage — email OTP, two steps (three with a second factor).
 *
 * §13 sets the security shape: email OTP with a 10-minute expiry and 5
 * attempts, JWT RS256, rate limits per firm and per IP. The UI's job is to make
 * that honest and fast.
 *
 * Things this page does on purpose:
 *
 *  - TWO STEPS, ONE SCREEN. Email → code. The email stays visible and editable
 *    ("wrong address?") because mistyping it is the single most common failure
 *    and forcing a back-navigation to fix it is hostile.
 *
 *  - DEV OTP ECHO, LABELLED. With no mailer installed the API returns the code
 *    instead of sending mail, so the product runs with zero SMTP config. We show
 *    it in a box that says exactly what it is and that it will not appear in
 *    production.
 *
 *  - MATCHED TO THE SERVER'S ERROR CONTRACT, CODE BY CODE. `POST /auth/verify`
 *    answers ONE code for every wrong/expired/used-up/never-issued code —
 *    `otp_invalid` — precisely so the response cannot say which. This page
 *    therefore does not count "tries left" (a number the server refuses to
 *    reveal, and one a reload would reset while the server's cap stayed), and
 *    it has no branch for codes the server never emits. The codes it DOES
 *    branch on: `otp_invalid`, `otp_rate_limited`/`rate_limited` (the resend
 *    countdown adopts the server's Retry-After), `account_unknown`, and
 *    `two_factor_required`, which carries the challenge the third step posts
 *    back with an authenticator code.
 *
 *  - AN INVITE LINK EXPLAINS ITSELF. Opened from `/login?invite=<token>` the
 *    page asks the API what the link points at and says so — who is asking,
 *    for which practice — or that it has lapsed or been withdrawn. Accepting is
 *    still the ordinary sign-in below: control of the mailbox is the credential,
 *    never the link. (There is no mobile field any more: the old one was
 *    collected and silently discarded, which is a trust bug, not a feature.)
 *
 *  - NEVER BLAMES THE USER. A wrong code is "That code didn't work", not
 *    "Invalid OTP".
 *
 *  - SIGN UP IS A THIRD MODE, NOT A FLAG ON VERIFY. `POST /auth/signup` creates
 *    the firm and its first admin and then issues a code; `POST /auth/verify`
 *    takes `{email, code}` ONLY (it is declared `extra="forbid"`). Both modes
 *    converge on the same code step.
 */

import { useEffect, useRef, useState } from 'react';
import { Badge, Button, Card, Field, Icon, Input, OtpInput } from '@garh/ui';
import { ProblemPanel, toProblem } from '../components';
import type { Problem } from '../components';
import { api } from '../lib/api';
import type { InviteStatus } from '../lib/api';
import { AppError, ERROR_CODES } from '../lib/errors';
import { useSessionStore } from '../stores/session';
import type { OtpRequestResult } from './_contracts';

export interface LoginPageProps {
  /** Called after a successful verify. The router owns where to go next. */
  onSignedIn?: (() => void) | undefined;
  /** The `?invite=` token from an invite email, if the page was opened from one. */
  inviteToken?: string | undefined;
}

type Step = 'email' | 'signup' | 'code' | 'twofactor';

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** What the invite banner has to say, per status the API can answer with. */
export function describeInvite(invite: InviteStatus): { title: string; detail: string } {
  const who = invite.invitedByName ?? invite.firmName;
  switch (invite.status) {
    case 'pending':
      return {
        title: `${who} invited you to join ${invite.firmName}`,
        detail: `You'll join as ${invite.role === 'admin' ? 'an admin' : 'a member'}. Sign in with ${invite.email} and the seat is yours — we'll email you a code.`,
      };
    case 'expired':
      return {
        title: 'This invite has expired',
        detail: `Invite links last a week. Ask ${who} to send a fresh one from the Team page of ${invite.firmName}.`,
      };
    case 'revoked':
      return {
        title: 'This invite was withdrawn',
        detail: `${invite.firmName} withdrew it. If that is a surprise, ask ${who}.`,
      };
    case 'accepted':
      return {
        title: 'This invite has already been used',
        detail: `Sign in with ${invite.email} as usual — the seat at ${invite.firmName} is already yours.`,
      };
    default:
      return { title: 'About this invite', detail: `From ${invite.firmName}.` };
  }
}

/** The one place a server error becomes a decision on the code step. */
export function classifyVerifyError(error: AppError): {
  kind: 'otp' | 'account_unknown' | 'two_factor' | 'other';
  challenge?: string;
} {
  if (error.code === ERROR_CODES.otpInvalid) return { kind: 'otp' };
  if (error.code === ERROR_CODES.accountUnknown) return { kind: 'account_unknown' };
  if (error.code === ERROR_CODES.twoFactorRequired) {
    const challenge = error.data.challenge;
    if (typeof challenge === 'string' && challenge.length > 0) {
      return { kind: 'two_factor', challenge };
    }
  }
  return { kind: 'other' };
}

export function LoginPage({ onSignedIn, inviteToken }: LoginPageProps): JSX.Element {
  const requestOtp = useSessionStore((s) => s.requestOtp);
  const verifyOtp = useSessionStore((s) => s.verifyOtp);
  const signUp = useSessionStore((s) => s.signUp);
  const completeTwoFactor = useSessionStore((s) => s.completeTwoFactor);

  const [step, setStep] = useState<Step>('email');
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [firmName, setFirmName] = useState('');
  const [personName, setPersonName] = useState('');
  const [coaNumber, setCoaNumber] = useState('');
  const [secondFactor, setSecondFactor] = useState('');
  const [challenge, setChallenge] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [fieldError, setFieldError] = useState<string | undefined>(undefined);
  const [otpMeta, setOtpMeta] = useState<OtpRequestResult | null>(null);
  const [resendIn, setResendIn] = useState(0);
  const [invite, setInvite] = useState<InviteStatus | null>(null);
  const [inviteProblem, setInviteProblem] = useState<string | null>(null);

  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Resend countdown. Kept in the page rather than the store: it is a piece of
  // screen state, and a store that ticks every second re-renders the world.
  useEffect(() => {
    if (resendIn <= 0) return;
    timerRef.current = setInterval(() => {
      setResendIn((v) => (v <= 1 ? 0 : v - 1));
    }, 1000);
    return () => {
      if (timerRef.current !== null) clearInterval(timerRef.current);
    };
  }, [resendIn]);

  // An invite link: ask what it points at, and pre-fill the address it names.
  useEffect(() => {
    if (inviteToken === undefined || inviteToken === '') return;
    let cancelled = false;
    api.auth
      .inviteStatus(inviteToken)
      .then((status) => {
        if (cancelled) return;
        setInvite(status);
        setEmail((current) => (current === '' ? status.email : current));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const p = toProblem(err);
        setInviteProblem(
          p.code === ERROR_CODES.inviteInvalid
            ? "This invite link isn't valid. Ask whoever invited you for a fresh one — or sign in as usual below."
            : p.message,
        );
      });
    return () => {
      cancelled = true;
    };
  }, [inviteToken]);

  /** A 429 on send carries the server's own countdown; adopt it instead of guessing. */
  const adoptRateLimit = (err: unknown): boolean => {
    const error = AppError.from(err);
    if (error.code !== ERROR_CODES.otpRateLimited && error.code !== ERROR_CODES.rateLimited) {
      return false;
    }
    const wait = error.retryAfterSeconds ?? 60;
    setResendIn(wait);
    setFieldError(`${error.message} You can ask again in ${wait} second${wait === 1 ? '' : 's'}.`);
    return true;
  };

  const sendCode = async (): Promise<void> => {
    const trimmed = email.trim().toLowerCase();
    if (!EMAIL_RE.test(trimmed)) {
      setFieldError('That does not look like an email address. Check for a typo?');
      return;
    }
    setFieldError(undefined);
    setProblem(null);
    setBusy(true);
    try {
      const result = await requestOtp(trimmed);
      setOtpMeta(result);
      setResendIn(result.resendAfterSeconds);
      setCode('');
      setStep('code');
    } catch (err) {
      if (adoptRateLimit(err)) {
        // The code they already have is still good for ten minutes: stay (or
        // land) on the code step rather than bouncing them to the address.
        if (step === 'email') setStep('code');
      } else {
        setProblem(toProblem(err));
      }
    } finally {
      setBusy(false);
    }
  };

  const createFirm = async (): Promise<void> => {
    const trimmed = email.trim().toLowerCase();
    if (firmName.trim().length < 2) {
      setFieldError('What is the practice called? It goes on every drawing.');
      return;
    }
    if (personName.trim().length < 2) {
      setFieldError('And your name? It becomes the architect of record by default.');
      return;
    }
    if (!EMAIL_RE.test(trimmed)) {
      setFieldError('That does not look like an email address. Check for a typo?');
      return;
    }
    setFieldError(undefined);
    setProblem(null);
    setBusy(true);
    try {
      const result = await signUp({
        firmName,
        name: personName,
        email: trimmed,
        ...(coaNumber.trim() === '' ? {} : { coaNumber }),
      });
      setOtpMeta(result);
      setResendIn(result.resendAfterSeconds);
      setCode('');
      setStep('code');
    } catch (err) {
      const p = toProblem(err);
      if (p.code === ERROR_CODES.emailAlreadyRegistered) {
        // The one place the API admits an address exists — say so plainly and
        // put them on the path that works instead of repeating the form.
        setStep('email');
        setFieldError(
          'That address already has an account. Sign in instead — we will email you a code.',
        );
      } else if (!adoptRateLimit(err)) {
        setProblem(p);
      }
    } finally {
      setBusy(false);
    }
  };

  const submitCode = async (value: string): Promise<void> => {
    if (value.length < 6) {
      setFieldError('The code is six digits.');
      return;
    }
    setFieldError(undefined);
    setProblem(null);
    setBusy(true);
    try {
      await verifyOtp(email.trim().toLowerCase(), value);
      onSignedIn?.();
    } catch (err) {
      const error = AppError.from(err);
      const outcome = classifyVerifyError(error);
      if (outcome.kind === 'otp') {
        // The server says nothing more than "no" — by design (§13). Don't invent
        // a tries-left number it refused to give us.
        setCode('');
        setFieldError(
          "That code didn't work — it may be mistyped, expired, or already used. Check the newest email, or send a fresh code.",
        );
      } else if (outcome.kind === 'account_unknown') {
        setStep('signup');
        setCode('');
        setOtpMeta(null);
        setFieldError(
          'You proved that address, but there is no practice behind it any more. Create one to continue.',
        );
      } else if (outcome.kind === 'two_factor' && outcome.challenge !== undefined) {
        setChallenge(outcome.challenge);
        setSecondFactor('');
        setStep('twofactor');
      } else {
        setProblem(toProblem(error));
      }
    } finally {
      setBusy(false);
    }
  };

  const submitSecondFactor = async (): Promise<void> => {
    const value = secondFactor.trim();
    if (challenge === null) {
      setStep('email');
      return;
    }
    if (value.length < 6) {
      setFieldError('Enter the six-digit code from your authenticator app, or a recovery code.');
      return;
    }
    setFieldError(undefined);
    setProblem(null);
    setBusy(true);
    try {
      await completeTwoFactor(challenge, value);
      onSignedIn?.();
    } catch (err) {
      const error = AppError.from(err);
      if (error.code === ERROR_CODES.twoFactorInvalid) {
        if (/expired/i.test(error.message)) {
          // The five-minute challenge lapsed: the only way back is a new code.
          setChallenge(null);
          setSecondFactor('');
          setStep('email');
          setFieldError('That sign-in attempt expired. Ask for a new code and start again.');
        } else {
          setSecondFactor('');
          setFieldError(
            "That code didn't work. Try the next one your app shows, or a recovery code.",
          );
        }
      } else {
        setProblem(toProblem(error));
      }
    } finally {
      setBusy(false);
    }
  };

  const inviteBanner =
    invite !== null ? (
      <div
        className="mb-4 rounded-md border border-brand/40 bg-brand-soft p-3"
        data-testid="invite-banner"
        data-status={invite.status}
      >
        <div className="flex items-center gap-2">
          <Icon name="users" size={14} />
          <span className="text-sm font-medium text-ink">{describeInvite(invite).title}</span>
        </div>
        <p className="mt-1 text-xs leading-5 text-ink-muted">{describeInvite(invite).detail}</p>
      </div>
    ) : inviteProblem !== null ? (
      <div
        className="mb-4 rounded-md border border-warn-line bg-warn-soft p-3"
        data-testid="invite-banner"
        data-status="invalid"
      >
        <p className="text-xs leading-5 text-warn-ink">{inviteProblem}</p>
      </div>
    ) : null;

  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-2 text-center">
          <span
            className="flex h-11 w-11 items-center justify-center rounded-xl bg-brand text-brand-fg"
            aria-hidden="true"
          >
            <Icon name="home" size={22} />
          </span>
          <h1 className="text-xl font-semibold text-ink">Garh AI</h1>
          <p className="text-sm text-ink-muted">
            Compliant house designs, from plot to drawing set.
          </p>
        </div>

        <Card className="p-5">
          {inviteBanner}
          {problem !== null ? (
            <div className="mb-4">
              <ProblemPanel problem={problem} onRetry={() => setProblem(null)} />
            </div>
          ) : null}

          {step === 'email' ? (
            <form
              className="flex flex-col gap-4"
              onSubmit={(e) => {
                e.preventDefault();
                void sendCode();
              }}
            >
              <div>
                <h2 className="text-base font-semibold text-ink">Sign in</h2>
                <p className="mt-0.5 text-sm text-ink-muted">
                  We will email you a six-digit code. No password to remember.
                </p>
              </div>

              <Field label="Work email" required error={fieldError}>
                {({ id, describedBy, invalid }) => (
                  <Input
                    id={id}
                    type="email"
                    inputMode="email"
                    autoComplete="email"
                    autoFocus
                    iconLeft="mail"
                    placeholder="you@studio.in"
                    value={email}
                    aria-describedby={describedBy}
                    invalid={invalid}
                    onChange={(e) => {
                      setEmail(e.target.value);
                      if (fieldError !== undefined) setFieldError(undefined);
                    }}
                  />
                )}
              </Field>

              <Button
                type="submit"
                variant="primary"
                fullWidth
                loading={busy}
                loadingLabel="Sending your code"
              >
                Send me a code
              </Button>

              <p className="text-center text-xs text-ink-muted">
                New practice?{' '}
                <button
                  type="button"
                  className="garh-focus-ring rounded-sm text-brand-ink underline underline-offset-2 hover:text-brand"
                  onClick={() => {
                    setStep('signup');
                    setFieldError(undefined);
                    setProblem(null);
                  }}
                >
                  Create an account
                </button>
              </p>

              <p className="text-center text-2xs leading-4 text-ink-subtle">
                By signing in you agree that Garh AI&apos;s compliance checks are advisory. Drawings
                still need an architect of record.
              </p>
            </form>
          ) : step === 'signup' ? (
            <form
              className="flex flex-col gap-4"
              onSubmit={(e) => {
                e.preventDefault();
                void createFirm();
              }}
            >
              <div>
                <h2 className="text-base font-semibold text-ink">Create your practice</h2>
                <p className="mt-0.5 text-sm text-ink-muted">
                  One firm, then invite the rest of the studio from Settings → Team. We will email
                  you a code to finish — there is no password.
                </p>
              </div>

              <Field
                label="Practice name"
                required
                hint="Appears in the title block of every sheet."
              >
                {({ id, describedBy, invalid }) => (
                  <Input
                    id={id}
                    autoFocus
                    autoComplete="organization"
                    placeholder="Studio Vaastu Associates"
                    value={firmName}
                    aria-describedby={describedBy}
                    invalid={invalid}
                    onChange={(e) => {
                      setFirmName(e.target.value);
                      if (fieldError !== undefined) setFieldError(undefined);
                    }}
                  />
                )}
              </Field>

              <Field label="Your name" required>
                {({ id, describedBy, invalid }) => (
                  <Input
                    id={id}
                    autoComplete="name"
                    placeholder="Ar. Priya Menon"
                    value={personName}
                    aria-describedby={describedBy}
                    invalid={invalid}
                    onChange={(e) => {
                      setPersonName(e.target.value);
                      if (fieldError !== undefined) setFieldError(undefined);
                    }}
                  />
                )}
              </Field>

              <Field label="Work email" required error={fieldError}>
                {({ id, describedBy, invalid }) => (
                  <Input
                    id={id}
                    type="email"
                    inputMode="email"
                    autoComplete="email"
                    iconLeft="mail"
                    placeholder="you@studio.in"
                    value={email}
                    aria-describedby={describedBy}
                    invalid={invalid}
                    onChange={(e) => {
                      setEmail(e.target.value);
                      if (fieldError !== undefined) setFieldError(undefined);
                    }}
                  />
                )}
              </Field>

              <Field
                label="CoA number (optional)"
                hint="Council of Architecture registration. Municipal sheets need it; you can add it later under Settings → Account."
              >
                {({ id, describedBy }) => (
                  <Input
                    id={id}
                    placeholder="CA/2019/12345"
                    value={coaNumber}
                    aria-describedby={describedBy}
                    onChange={(e) => setCoaNumber(e.target.value)}
                  />
                )}
              </Field>

              <Button
                type="submit"
                variant="primary"
                fullWidth
                loading={busy}
                loadingLabel="Creating your practice"
              >
                Create account
              </Button>

              <p className="text-center text-xs text-ink-muted">
                Already have an account?{' '}
                <button
                  type="button"
                  className="garh-focus-ring rounded-sm text-brand-ink underline underline-offset-2 hover:text-brand"
                  onClick={() => {
                    setStep('email');
                    setFieldError(undefined);
                    setProblem(null);
                  }}
                >
                  Sign in
                </button>
              </p>
            </form>
          ) : step === 'twofactor' ? (
            <form
              className="flex flex-col gap-4"
              onSubmit={(e) => {
                e.preventDefault();
                void submitSecondFactor();
              }}
            >
              <div>
                <h2 className="text-base font-semibold text-ink">One more step</h2>
                <p className="mt-0.5 text-sm text-ink-muted">
                  This account has two-factor sign-in turned on. Enter the six-digit code from your
                  authenticator app, or one of your recovery codes.
                </p>
              </div>

              <Field label="Authenticator code" required error={fieldError}>
                {({ id, describedBy, invalid }) => (
                  <Input
                    id={id}
                    inputMode="text"
                    autoComplete="one-time-code"
                    autoFocus
                    iconLeft="shield"
                    placeholder="123 456"
                    value={secondFactor}
                    aria-describedby={describedBy}
                    invalid={invalid}
                    onChange={(e) => {
                      setSecondFactor(e.target.value);
                      if (fieldError !== undefined) setFieldError(undefined);
                    }}
                  />
                )}
              </Field>

              <Button
                type="submit"
                variant="primary"
                fullWidth
                loading={busy}
                loadingLabel="Checking your code"
                disabled={secondFactor.trim().length < 6}
              >
                Sign in
              </Button>

              <button
                type="button"
                className="garh-focus-ring self-center rounded-sm text-xs text-ink-muted underline underline-offset-2 hover:text-ink"
                onClick={() => {
                  setChallenge(null);
                  setSecondFactor('');
                  setFieldError(undefined);
                  setStep('email');
                }}
              >
                Start over
              </button>
            </form>
          ) : (
            <form
              className="flex flex-col gap-4"
              onSubmit={(e) => {
                e.preventDefault();
                void submitCode(code);
              }}
            >
              <div>
                <h2 className="text-base font-semibold text-ink">Check your email</h2>
                <p className="mt-0.5 text-sm text-ink-muted">
                  We sent a six-digit code to <span className="font-medium text-ink">{email}</span>.
                </p>
                {/* Shown to EVERYONE on this screen, on purpose. The API answers "sent"
                    for an address it has never seen (§13: it must not reveal which
                    emails are registered), so a newcomer who typed their address into
                    sign-in reaches this screen and waits for a code that is not
                    coming. This line is the only way to tell them without telling an
                    attacker anything — it says the same thing whether the address
                    exists or not. Execution find on the first live trial sign-in. */}
                <p className="mt-2 text-xs leading-5 text-ink-muted">
                  Nothing after a minute? Sign-in only works for practices that already have an
                  account, or for an address a practice has invited. If you&apos;re new,{' '}
                  <button
                    type="button"
                    className="garh-focus-ring rounded-sm text-brand-ink underline underline-offset-2 hover:text-brand"
                    onClick={() => {
                      setStep('signup');
                      setCode('');
                      setFieldError(undefined);
                      setOtpMeta(null);
                      setProblem(null);
                    }}
                  >
                    create one first
                  </button>
                  .
                </p>
              </div>

              {otpMeta?.devCode === undefined ? null : (
                <div className="rounded-md border border-info-line bg-info-soft p-3">
                  <div className="flex items-center gap-2">
                    <Badge tone="info" icon="info">
                      Development
                    </Badge>
                    <span className="text-xs text-info-ink">Email sending is switched off</span>
                  </div>
                  <p className="mt-1.5 text-xs leading-5 text-info-ink">
                    No mail was sent. Your code is{' '}
                    <code className="rounded bg-surface px-1 py-0.5 font-mono text-sm font-semibold tracking-widest text-ink garh-nums">
                      {otpMeta.devCode}
                    </code>
                    . This box only appears when the server runs with no email transport — it is
                    never shown in staging or production.
                  </p>
                  <Button
                    size="sm"
                    variant="secondary"
                    className="mt-2"
                    onClick={() => {
                      setCode(otpMeta.devCode ?? '');
                      void submitCode(otpMeta.devCode ?? '');
                    }}
                  >
                    Use this code
                  </Button>
                </div>
              )}

              <OtpInput
                value={code}
                onChange={(v) => {
                  setCode(v);
                  if (fieldError !== undefined) setFieldError(undefined);
                }}
                onComplete={(v) => void submitCode(v)}
                error={fieldError}
                autoFocus
                disabled={busy}
              />

              <Button
                type="submit"
                variant="primary"
                fullWidth
                loading={busy}
                loadingLabel="Checking your code"
                disabled={code.length < 6}
              >
                Sign in
              </Button>

              <div className="flex items-center justify-between gap-2 text-xs">
                <button
                  type="button"
                  className="garh-focus-ring rounded-sm text-ink-muted underline underline-offset-2 hover:text-ink"
                  onClick={() => {
                    setStep('email');
                    setCode('');
                    setFieldError(undefined);
                    setOtpMeta(null);
                  }}
                >
                  Wrong address?
                </button>
                <button
                  type="button"
                  disabled={resendIn > 0 || busy}
                  className="garh-focus-ring rounded-sm text-brand-ink underline underline-offset-2 hover:text-brand disabled:cursor-not-allowed disabled:text-ink-subtle disabled:no-underline"
                  onClick={() => void sendCode()}
                >
                  {resendIn > 0 ? `Send again in ${resendIn}s` : 'Send a fresh code'}
                </button>
              </div>

              <p className="text-center text-2xs text-ink-subtle">
                Codes last ten minutes and five attempts. Nothing else on your account changes if
                one expires.
              </p>
            </form>
          )}
        </Card>
      </div>
    </div>
  );
}

export default LoginPage;
