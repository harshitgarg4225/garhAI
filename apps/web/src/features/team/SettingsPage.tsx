/**
 * SettingsPage — `/settings/:section` (J01 "set up the practice").
 *
 * Three sections under one frame:
 *
 *   practice  the firm's identity — name, address, GSTIN, registration — and the
 *             title-block template the sheets already print (edited through the
 *             same `PUT /firm/drawing-preferences` the sheets read from, so what
 *             this page shows is what prints);
 *   team      members with role, seat and last sign-in; invites with resend and
 *             withdraw; seats used against the plan;
 *   account   your own name and CoA number, signed-in devices, sign out
 *             everywhere, and the second factor.
 *
 * Until this existed the sign-up copy promised "invite the rest of the studio" and
 * "add your CoA later in firm settings", and neither place was reachable from any
 * screen. The section is a route segment, not local state, so a link to
 * `/settings/team` from anywhere (the invite toast, an email) lands on the tab.
 */

import { Link, Navigate, useNavigate, useParams } from 'react-router-dom';
import { TabLinks, type TabLinkItem } from '@garh/ui';

import { AppShell, PageBody, PageHeader } from '../../components';
import { useSessionStore } from '../../stores/session';
import { AccountSection } from './AccountSection';
import { PracticeSection } from './PracticeSection';
import { TeamSection } from './TeamSection';

export const SETTINGS_SECTIONS = ['practice', 'team', 'account'] as const;
export type SettingsSection = (typeof SETTINGS_SECTIONS)[number];

function isSection(value: string | undefined): value is SettingsSection {
  return value !== undefined && (SETTINGS_SECTIONS as readonly string[]).includes(value);
}

const TABS: readonly TabLinkItem[] = [
  { key: 'practice', label: 'Practice', href: '/settings/practice', icon: 'home' },
  { key: 'team', label: 'Team', href: '/settings/team', icon: 'users' },
  { key: 'account', label: 'Account', href: '/settings/account', icon: 'user' },
];

export function SettingsPage(): JSX.Element {
  const { section } = useParams();
  const navigate = useNavigate();
  const user = useSessionStore((s) => s.user);
  const firm = useSessionStore((s) => s.firm);
  const signOut = useSessionStore((s) => s.signOut);

  if (!isSection(section)) return <Navigate to="/settings/practice" replace />;

  return (
    <AppShell
      firmName={firm?.name}
      userName={user?.name}
      onSignOut={() => void signOut()}
      renderHomeLink={({ className, children }) => (
        <Link to="/" className={className}>
          {children}
        </Link>
      )}
    >
      <PageBody className="max-w-4xl">
        <PageHeader
          title="Settings"
          description="Your practice, the people in it, and your own account."
        />
        <TabLinks
          items={TABS}
          activeKey={section}
          label="Settings sections"
          className="mb-6"
          renderLink={({ href, className, children, 'aria-current': ariaCurrent }) => (
            <Link to={href} className={className} aria-current={ariaCurrent}>
              {children}
            </Link>
          )}
        />
        {section === 'practice' ? (
          <PracticeSection />
        ) : section === 'team' ? (
          <TeamSection />
        ) : (
          <AccountSection onSignedOutEverywhere={() => navigate('/login', { replace: true })} />
        )}
      </PageBody>
    </AppShell>
  );
}

export default SettingsPage;
