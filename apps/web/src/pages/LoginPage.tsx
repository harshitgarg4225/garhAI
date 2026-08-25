/**
 * LoginPage — email OTP, two steps.
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
 *  - DEV OTP ECHO, LABELLED. With `PROVIDER_EMAIL=mock` the API returns the
 *    code instead of sending mail, so the product runs with zero SMTP config.
 *    We show it in a box that says exactly what it is and that it will not
 *    appear in production. Hiding it would mean every developer digs through
 *    docker logs; showing it unlabelled would look like a leak.
 *
 *  - +91 PHONE FIELD STYLING (§15) even though auth is by email. Firms are
 *    reached on WhatsApp, so the optional mobile field is part of first sign-in
 *    and is styled the Indian way: fixed +91 prefix, 10 digits, "98765 43210"
 *    grouping. It is optional and says why we want it.
 *
 *  - NEVER BLAMES THE USER. A wrong code is "That code didn't match" with the
 *    attempts left, not "Invalid OTP".
 *
 *  - SIGN UP IS A THIRD MODE, NOT A FLAG ON VERIFY. `POST /auth/signup` creates
 *    the firm and its first admin and then issues a code; `POST /auth/verify`
 *    takes `{email, code}` ONLY (it is declared `extra="forbid"`, so passing a
 *    firm name there is a 422). Both modes therefore converge on the same code
 *    step. Without this branch there was no way to create a firm from the web
 *    app at all, and on a fresh database every sign-in attempt dead-ended.
 *
 * STORE CONTRACT: `../stores/session` must export `useSessionStore` satisfying
 * `SessionSlice` in `./_contracts`.
 */

import { useEffect, useRef, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  Field,
  Icon,
  Input,
  OtpInput,
  PhoneInput,
  isPlausibleIndianMobile,
} from '@garh/ui';
import { ProblemPanel, toProblem } from '../components';
import type { Problem } from '../components';
import { useSessionStore } from '../stores/session';
import type { OtpRequestResult } from './_contracts';

export interface LoginPageProps {
  /** Called after a successful verify. The router owns where to go next. */
  onSignedIn?: (() => void) | undefined;
}

type Step = 'email' | 'signup' | 'code';

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function LoginPage({ onSignedIn }: LoginPageProps): JSX.Element {
  const requestOtp = useSessionStore((s) => s.requestOtp);
  const verifyOtp = useSessionStore((s) => s.verifyOtp);
  const signUp = useSessionStore((s) => s.signUp);

  const [step, setStep] = useState<Step>('email');
  const [email, setEmail] = useState('');
  const [mobile, setMobile] = useState('');
  const [code, setCode] = useState('');
  const [firmName, setFirmName] = useState('');
  const [personName, setPersonName] = useState('');
  const [coaNumber, setCoaNumber] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [fieldError, setFieldError] = useState<string | undefined>(undefined);
  const [otpMeta, setOtpMeta] = useState<OtpRequestResult | null>(null);
  const [resendIn, setResendIn] = useState(0);
  const [attemptsLeft, setAttemptsLeft] = useState(5);

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
      setAttemptsLeft(5);
      setCode('');
      setStep('code');
    } catch (err) {
      setProblem(toProblem(err));
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
      setAttemptsLeft(5);
      setCode('');
      setStep('code');
    } catch (err) {
      const p = toProblem(err);
      if (p.code === 'email_already_registered') {
        // The one place the API admits an address exists — say so plainly and
        // put them on the path that works instead of repeating the form.
        setStep('email');
        setFieldError(
          'That address already has an account. Sign in instead — we will email you a code.',
        );
      } else {
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
      const p = toProblem(err);
      if (p.code === 'otp_mismatch' || p.code === 'otp_invalid') {
        const left = Math.max(0, attemptsLeft - 1);
        setAttemptsLeft(left);
        setCode('');
        setFieldError(
          left === 0
            ? 'That code did not match, and this one is now used up. Send a fresh code to try again.'
            : `That code did not match. ${left} ${left === 1 ? 'try' : 'tries'} left, or send a fresh code.`,
        );
      } else if (p.code === 'otp_expired') {
        setFieldError('That code has expired — they last ten minutes. Send a fresh one.');
        setResendIn(0);
      } else {
        setProblem(p);
      }
    } finally {
      setBusy(false);
    }
  };

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

              <PhoneInput
                value={mobile}
                onChange={setMobile}
                label="Mobile (optional)"
                hint="Only used to send drawings to clients on WhatsApp. We never text you codes."
                error={
                  mobile.length > 0 && mobile.length < 10
                    ? 'An Indian mobile number is ten digits.'
                    : mobile.length === 10 && !isPlausibleIndianMobile(mobile)
                      ? 'Indian mobile numbers start with 6, 7, 8 or 9.'
                      : undefined
                }
              />

              <Button type="submit" variant="primary" fullWidth loading={busy} loadingLabel="Sending your code">
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
                  One firm, then invite the rest of the studio. We will email you a code to
                  finish — there is no password.
                </p>
              </div>

              <Field label="Practice name" required hint="Appears in the title block of every sheet.">
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
                hint="Council of Architecture registration. Municipal sheets need it, but you can add it later in firm settings."
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
                    . This box only appears when the server runs with a mock email provider — it is
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
                disabled={busy || attemptsLeft === 0}
              />

              <Button
                type="submit"
                variant="primary"
                fullWidth
                loading={busy}
                loadingLabel="Checking your code"
                disabled={code.length < 6 || attemptsLeft === 0}
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
                Codes last ten minutes. Nothing else on your account changes if one expires.
              </p>
            </form>
          )}
        </Card>
      </div>
    </div>
  );
}

export default LoginPage;
