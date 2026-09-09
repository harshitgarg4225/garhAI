/**
 * team — the practice's settings surface (J01): Practice · Team · Account.
 *
 * Mounted at `/settings/:section`. Everything here talks to `api.team`,
 * `api.auth.sessions`, `api.auth.twoFactor` and `api.sheets.preferences`.
 */

export { SettingsPage, SETTINGS_SECTIONS } from './SettingsPage';
export type { SettingsSection } from './SettingsPage';
export { PracticeSection } from './PracticeSection';
export { TeamSection, describeInviteRefusal, describeLastSignIn } from './TeamSection';
export { AccountSection, describeDevice } from './AccountSection';
export type { AccountSectionProps } from './AccountSection';
