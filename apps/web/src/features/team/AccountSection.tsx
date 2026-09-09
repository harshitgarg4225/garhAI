/**
 * AccountSection — your own name and CoA number, your devices, your second factor.
 *
 * The devices list is the F-3 surface the API has had all along with nothing
 * calling it: every refresh family signed in as you, the one you are reading it
 * on marked, each one revocable, plus "sign out everywhere" — which is the
 * store's own `signOut({ everywhere: true })`, i.e. `POST /auth/logout-all`,
 * the generation bump that kills every access AND refresh token at once.
 *
 * Two-factor (F-4): enrol shows the secret and the otpauth URI ONCE, activation
 * needs a live code and answers with recovery codes shown ONCE; disabling needs
 * a code too. Nothing here is ever re-read from the server for display.
 */

import { useEffect, useState } from 'react';
import { Badge, Button, Card, CardBody, CardHeader, Field, Input, useToast } from '@garh/ui';

import { ProblemPanel, toProblem } from '../../components';
import type { Problem } from '../../components';
import { api } from '../../lib/api';
import type { Device, TwoFactorEnrolment, TwoFactorStatus } from '../../lib/api';
import { AppError, ERROR_CODES } from '../../lib/errors';
import { useSessionStore } from '../../stores/session';

export interface AccountSectionProps {
  /** After `POST /auth/logout-all` the session is gone; the router goes to /login. */
  onSignedOutEverywhere?: (() => void) | undefined;
}

export function describeDevice(device: Device): string {
  const when = new Date(device.lastUsedAt * 1000);
  const stamp = Number.isNaN(when.getTime()) ? '' : ` · active ${when.toLocaleString('en-IN')}`;
  return `${device.device}${device.ip === null ? '' : ` · ${device.ip}`}${stamp}`;
}

export function AccountSection({ onSignedOutEverywhere }: AccountSectionProps): JSX.Element {
  const user = useSessionStore((s) => s.user);
  const updateProfile = useSessionStore((s) => s.updateProfile);
  const signOut = useSessionStore((s) => s.signOut);
  const { toast } = useToast();

  const [name, setName] = useState(user?.name ?? '');
  const [coa, setCoa] = useState(user?.coaNumber ?? '');
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileError, setProfileError] = useState<string | undefined>(undefined);

  const [devices, setDevices] = useState<Device[] | null>(null);
  const [devicesProblem, setDevicesProblem] = useState<Problem | null>(null);
  const [busyDevice, setBusyDevice] = useState<string | null>(null);
  const [signingOutAll, setSigningOutAll] = useState(false);

  const [twoFactor, setTwoFactor] = useState<TwoFactorStatus | null>(null);
  const [enrolment, setEnrolment] = useState<TwoFactorEnrolment | null>(null);
  const [factorCode, setFactorCode] = useState('');
  const [factorError, setFactorError] = useState<string | undefined>(undefined);
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [factorBusy, setFactorBusy] = useState(false);
  const [disabling, setDisabling] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setDevicesProblem(null);
    api.auth.sessions
      .list()
      .then((items) => {
        if (!cancelled) setDevices(items);
      })
      .catch((err: unknown) => {
        if (!cancelled) setDevicesProblem(toProblem(err));
      });
    api.auth.twoFactor
      .status()
      .then((status) => {
        if (!cancelled) setTwoFactor(status);
      })
      .catch(() => {
        if (!cancelled) setTwoFactor(null);
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const saveProfile = async (): Promise<void> => {
    if (name.trim().length < 1) {
      setProfileError('Your name goes on drawings; it cannot be blank.');
      return;
    }
    setProfileError(undefined);
    setSavingProfile(true);
    try {
      await updateProfile({ name: name.trim(), coaNumber: coa.trim() });
      toast({ severity: 'pass', title: 'Profile saved' });
    } catch (err) {
      setProfileError(toProblem(err).message);
    } finally {
      setSavingProfile(false);
    }
  };

  const revokeDevice = async (device: Device): Promise<void> => {
    setBusyDevice(device.id);
    try {
      await api.auth.sessions.revoke(device.id);
      setDevices((current) => (current ?? []).filter((d) => d.id !== device.id));
      toast({ severity: 'pass', title: 'Signed that device out' });
    } catch (err) {
      toast({
        severity: 'warn',
        title: "Couldn't sign it out",
        description: toProblem(err).message,
      });
    } finally {
      setBusyDevice(null);
    }
  };

  const signOutOthers = async (): Promise<void> => {
    setBusyDevice('others');
    try {
      const ended = await api.auth.sessions.revokeOthers();
      setDevices((current) => (current ?? []).filter((d) => d.current));
      toast({
        severity: 'pass',
        title:
          ended === 0
            ? 'No other devices were signed in'
            : `Signed out ${ended} other device${ended === 1 ? '' : 's'}`,
      });
    } catch (err) {
      toast({
        severity: 'warn',
        title: "Couldn't sign them out",
        description: toProblem(err).message,
      });
    } finally {
      setBusyDevice(null);
    }
  };

  const signOutEverywhere = async (): Promise<void> => {
    setSigningOutAll(true);
    await signOut({ everywhere: true });
    onSignedOutEverywhere?.();
  };

  const startEnrolment = async (): Promise<void> => {
    setFactorBusy(true);
    setFactorError(undefined);
    try {
      setEnrolment(await api.auth.twoFactor.enrol());
      setFactorCode('');
    } catch (err) {
      setFactorError(toProblem(err).message);
    } finally {
      setFactorBusy(false);
    }
  };

  const activate = async (): Promise<void> => {
    if (factorCode.trim().length < 6) {
      setFactorError('Enter the six-digit code your authenticator app shows.');
      return;
    }
    setFactorBusy(true);
    setFactorError(undefined);
    try {
      const result = await api.auth.twoFactor.activate(factorCode.trim());
      setRecoveryCodes(result.recoveryCodes);
      setEnrolment(null);
      setFactorCode('');
      setReloadKey((k) => k + 1);
      toast({ severity: 'pass', title: 'Two-factor sign-in is on' });
    } catch (err) {
      const error = AppError.from(err);
      setFactorError(
        error.code === ERROR_CODES.twoFactorInvalid
          ? "That code didn't match. Try the next one your app shows."
          : error.message,
      );
    } finally {
      setFactorBusy(false);
    }
  };

  const disable = async (): Promise<void> => {
    if (factorCode.trim().length < 6) {
      setFactorError('Enter a code from your app, or a recovery code, to turn it off.');
      return;
    }
    setFactorBusy(true);
    setFactorError(undefined);
    try {
      setTwoFactor(await api.auth.twoFactor.disable(factorCode.trim()));
      setDisabling(false);
      setFactorCode('');
      toast({ severity: 'pass', title: 'Two-factor sign-in is off' });
    } catch (err) {
      const error = AppError.from(err);
      setFactorError(
        error.code === ERROR_CODES.twoFactorInvalid ? "That code didn't match." : error.message,
      );
    } finally {
      setFactorBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader
          title="Your profile"
          description={user === null ? undefined : `Signed in as ${user.email}.`}
        />
        <CardBody>
          <form
            className="flex flex-col gap-4"
            data-testid="profile-form"
            onSubmit={(e) => {
              e.preventDefault();
              void saveProfile();
            }}
          >
            <Field label="Your name" required error={profileError}>
              {({ id, describedBy, invalid }) => (
                <Input
                  id={id}
                  autoComplete="name"
                  value={name}
                  aria-describedby={describedBy}
                  invalid={invalid}
                  onChange={(e) => {
                    setName(e.target.value);
                    if (profileError !== undefined) setProfileError(undefined);
                  }}
                />
              )}
            </Field>
            <Field
              label="CoA number"
              hint="Council of Architecture registration. Municipal sheets print it beside your name. Leave blank to remove it."
            >
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  placeholder="CA/2019/12345"
                  value={coa}
                  aria-describedby={describedBy}
                  onChange={(e) => setCoa(e.target.value)}
                />
              )}
            </Field>
            <div className="flex justify-end">
              <Button type="submit" variant="primary" loading={savingProfile} loadingLabel="Saving">
                Save profile
              </Button>
            </div>
          </form>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Signed-in devices"
          description="Every browser signed in as you. Sign one out, or all of them."
          actions={
            <Button
              size="sm"
              variant="danger"
              iconLeft="log-out"
              loading={signingOutAll}
              loadingLabel="Signing out"
              onClick={() => void signOutEverywhere()}
            >
              Sign out everywhere
            </Button>
          }
        />
        <CardBody>
          {devicesProblem !== null ? (
            <ProblemPanel problem={devicesProblem} onRetry={() => setReloadKey((k) => k + 1)} />
          ) : devices === null ? (
            <p className="text-sm text-ink-muted" aria-busy="true">
              Loading devices…
            </p>
          ) : (
            <>
              <ul className="divide-y divide-line" data-testid="device-list">
                {devices.map((device) => (
                  <li key={device.id} className="flex items-center gap-3 py-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 text-sm text-ink">
                        <span className="truncate">{describeDevice(device)}</span>
                        {device.current ? <Badge tone="info">This device</Badge> : null}
                      </div>
                    </div>
                    {device.current ? null : (
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={busyDevice !== null}
                        onClick={() => void revokeDevice(device)}
                      >
                        Sign out
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
              {devices.filter((d) => !d.current).length > 0 ? (
                <div className="mt-3 flex justify-end">
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={busyDevice !== null}
                    onClick={() => void signOutOthers()}
                  >
                    Sign out the other devices
                  </Button>
                </div>
              ) : null}
            </>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Two-factor sign-in"
          description="A code from an authenticator app after the emailed one. Knowing your email code stops being enough."
          actions={
            twoFactor === null ? undefined : twoFactor.enabled ? (
              <Badge tone="pass" icon="shield-check">
                On · {twoFactor.recoveryCodesRemaining} recovery codes left
              </Badge>
            ) : (
              <Badge tone="neutral" icon="shield">
                Off
              </Badge>
            )
          }
        />
        <CardBody>
          {recoveryCodes !== null ? (
            <div className="mb-4 rounded-md border border-warn-line bg-warn-soft p-3">
              <p className="text-sm font-medium text-warn-ink">
                Save these recovery codes now — they are shown once.
              </p>
              <ul
                className="mt-2 grid grid-cols-2 gap-1 font-mono text-sm text-ink"
                data-testid="recovery-codes"
              >
                {recoveryCodes.map((code) => (
                  <li key={code}>{code}</li>
                ))}
              </ul>
              <Button
                size="sm"
                variant="ghost"
                className="mt-2"
                onClick={() => setRecoveryCodes(null)}
              >
                I have saved them
              </Button>
            </div>
          ) : null}

          {twoFactor === null ? (
            <p className="text-sm text-ink-muted">Two-factor status is unavailable right now.</p>
          ) : twoFactor.enabled && !disabling ? (
            <div className="flex justify-end">
              <Button size="sm" variant="secondary" onClick={() => setDisabling(true)}>
                Turn off
              </Button>
            </div>
          ) : twoFactor.enabled && disabling ? (
            <form
              className="flex flex-col gap-3"
              onSubmit={(e) => {
                e.preventDefault();
                void disable();
              }}
            >
              <Field label="Code from your app, or a recovery code" required error={factorError}>
                {({ id, describedBy, invalid }) => (
                  <Input
                    id={id}
                    autoComplete="one-time-code"
                    value={factorCode}
                    aria-describedby={describedBy}
                    invalid={invalid}
                    onChange={(e) => setFactorCode(e.target.value)}
                  />
                )}
              </Field>
              <div className="flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setDisabling(false)}>
                  Keep it on
                </Button>
                <Button
                  type="submit"
                  variant="danger"
                  loading={factorBusy}
                  loadingLabel="Turning off"
                >
                  Turn off two-factor
                </Button>
              </div>
            </form>
          ) : enrolment === null ? (
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm text-ink-muted">
                {factorError ?? 'Recommended for every admin.'}
              </p>
              <Button
                variant="primary"
                size="sm"
                loading={factorBusy}
                onClick={() => void startEnrolment()}
              >
                Turn on
              </Button>
            </div>
          ) : (
            <form
              className="flex flex-col gap-3"
              data-testid="enrol-form"
              onSubmit={(e) => {
                e.preventDefault();
                void activate();
              }}
            >
              <p className="text-sm text-ink">
                Add this to your authenticator app, then enter the code it shows.
              </p>
              <p
                className="break-all rounded-md bg-surface-muted p-2 font-mono text-sm text-ink"
                data-testid="enrol-secret"
              >
                {enrolment.secret}
              </p>
              <p className="break-all text-xs text-ink-muted">{enrolment.otpauthUri}</p>
              <Field label="Code from your app" required error={factorError}>
                {({ id, describedBy, invalid }) => (
                  <Input
                    id={id}
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    value={factorCode}
                    aria-describedby={describedBy}
                    invalid={invalid}
                    onChange={(e) => setFactorCode(e.target.value)}
                  />
                )}
              </Field>
              <div className="flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setEnrolment(null)}>
                  Cancel
                </Button>
                <Button
                  type="submit"
                  variant="primary"
                  loading={factorBusy}
                  loadingLabel="Checking"
                >
                  Activate
                </Button>
              </div>
            </form>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
