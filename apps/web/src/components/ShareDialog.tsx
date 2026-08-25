/**
 * ShareDialog + WhatsAppShareButton — the client share surface.
 *
 * §15: '"Share on WhatsApp" on renders/share links (wa.me deep link with
 * preformatted message)'. WhatsApp is how Indian architects actually send work
 * to clients, so it is the primary action here and email is not offered at all
 * in the MVP.
 *
 * Security notes that shape this UI (§13):
 *  - The link carries a random 256-bit token, stored hashed. It is shown ONCE
 *    per creation and can be revoked; the dialog says both things out loud.
 *  - Scope is explicit per section. The default is the least that is useful
 *    (plan + 3D + renders), not everything — a client does not need the
 *    compliance annexure by default.
 *  - We never prefill the client's phone number into the wa.me URL. The user
 *    picks the recipient inside WhatsApp, which keeps the number out of our URL
 *    and out of any analytics.
 *  - Creating a share link is audited server-side; the copy says so, because a
 *    surprise audit trail is worse than a declared one.
 */

import { useState } from 'react';
import { formatIndianDate } from '@garh/model';
import {
  Badge,
  Button,
  Dialog,
  Icon,
  Input,
  LinkButton,
  SelectField,
  buildShareMessage,
  copyToClipboard,
  useToast,
  whatsappShareUrl,
  cn,
} from '@garh/ui';

/** Mirrors `share_links.SHARE_SECTIONS` in the API repository layer. */
export const SHARE_SECTIONS = ['plan', 'three_d', 'renders', 'sheets', 'compliance'] as const;
export type ShareSection = (typeof SHARE_SECTIONS)[number];

const SECTION_LABEL: Readonly<Record<ShareSection, string>> = {
  plan: 'Floor plans',
  three_d: '3D view',
  renders: 'Renders',
  sheets: 'Drawing set',
  compliance: 'Compliance summary',
};

const SECTION_NOTE: Readonly<Record<ShareSection, string>> = {
  plan: 'Read-only plan viewer with room names and areas.',
  three_d: 'Orbit the model. No editing.',
  renders: 'The exterior and interior images you have generated.',
  sheets: 'The full municipal drawing set as PDF.',
  compliance: 'The bye-law and Vastu check results. Usually kept internal.',
};

export const DEFAULT_SHARE_SECTIONS: readonly ShareSection[] = ['plan', 'three_d', 'renders'];

export const SHARE_EXPIRY_OPTIONS = [
  { value: '7', label: '7 days' },
  { value: '30', label: '30 days' },
  { value: '90', label: '90 days' },
] as const;

export interface ShareDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectName: string;
  /** "1,200.0 sq ft · 133 gaj" — goes into the WhatsApp message. */
  plotSummary?: string | undefined;
  /** "G+1 · 3 BHK". */
  configuration?: string | undefined;
  firmName?: string | undefined;

  /**
   * The existing active link, if one has been created. `undefined` means the
   * dialog shows the create form.
   */
  shareUrl?: string | undefined;
  /** ISO 8601 expiry of the existing link. */
  expiresAt?: string | undefined;

  /** Create (or replace) the link. Resolves when the parent has the new URL. */
  onCreate: (input: { sections: ShareSection[]; expiryDays: number; canComment: boolean }) => void;
  creating?: boolean | undefined;
  onRevoke?: (() => void) | undefined;
  revoking?: boolean | undefined;
}

export function ShareDialog({
  open,
  onOpenChange,
  projectName,
  plotSummary,
  configuration,
  firmName,
  shareUrl,
  expiresAt,
  onCreate,
  creating = false,
  onRevoke,
  revoking = false,
}: ShareDialogProps): JSX.Element {
  const { toast } = useToast();
  const [sections, setSections] = useState<ShareSection[]>([...DEFAULT_SHARE_SECTIONS]);
  const [expiryDays, setExpiryDays] = useState<'7' | '30' | '90'>('30');
  const [canComment, setCanComment] = useState(true);

  const toggleSection = (section: ShareSection): void => {
    setSections((prev) =>
      prev.includes(section) ? prev.filter((s) => s !== section) : [...prev, section],
    );
  };

  const message =
    shareUrl === undefined
      ? ''
      : buildShareMessage({
          projectName,
          url: shareUrl,
          plotSummary,
          configuration,
          firmName,
          expiresOn: expiresAt === undefined ? undefined : formatIndianDate(expiresAt),
        });

  const handleCopy = async (): Promise<void> => {
    if (shareUrl === undefined) return;
    const ok = await copyToClipboard(shareUrl);
    if (ok) {
      toast({ severity: 'pass', title: 'Link copied' });
    } else {
      toast({
        severity: 'warn',
        title: "Couldn't copy automatically",
        description: 'Select the link in the box and copy it manually.',
      });
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={shareUrl === undefined ? 'Share with your client' : 'Client link is live'}
      description={
        shareUrl === undefined
          ? 'They get a read-only view in the browser. No sign-up, no app.'
          : 'Anyone with this link can view the sections you picked, until it expires or you revoke it.'
      }
      size="md"
      footer={
        shareUrl === undefined ? (
          <>
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              iconLeft="share"
              loading={creating}
              loadingLabel="Creating the link"
              disabled={sections.length === 0}
              onClick={() =>
                onCreate({ sections, expiryDays: Number(expiryDays), canComment })
              }
            >
              Create link
            </Button>
          </>
        ) : (
          <>
            {onRevoke === undefined ? null : (
              <Button variant="ghost" loading={revoking} onClick={onRevoke}>
                Revoke this link
              </Button>
            )}
            <Button variant="secondary" onClick={() => onOpenChange(false)}>
              Done
            </Button>
          </>
        )
      }
    >
      {shareUrl === undefined ? (
        <div className="flex flex-col gap-4">
          <fieldset>
            <legend className="mb-2 text-xs font-medium text-ink-muted">
              What can they see?
            </legend>
            <div className="flex flex-col gap-1.5">
              {SHARE_SECTIONS.map((section) => {
                const checked = sections.includes(section);
                return (
                  <label
                    key={section}
                    className={cn(
                      'flex cursor-pointer items-start gap-2.5 rounded-md border p-2.5 transition-colors',
                      checked ? 'border-brand/40 bg-brand-soft' : 'border-line hover:bg-surface-muted',
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleSection(section)}
                      className="garh-focus-ring mt-0.5 h-4 w-4 rounded border-line-strong accent-[rgb(var(--garh-brand))]"
                    />
                    <span className="min-w-0">
                      <span className="block text-sm font-medium text-ink">
                        {SECTION_LABEL[section]}
                      </span>
                      <span className="block text-xs text-ink-muted">{SECTION_NOTE[section]}</span>
                    </span>
                  </label>
                );
              })}
            </div>
            {sections.length === 0 ? (
              <p className="mt-2 text-xs text-warn-ink">
                Pick at least one section, otherwise there is nothing to open.
              </p>
            ) : null}
          </fieldset>

          <SelectField
            label="Link expires in"
            value={expiryDays}
            onValueChange={(v) => setExpiryDays(v)}
            options={SHARE_EXPIRY_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
            hint="You can revoke it earlier at any time."
          />

          <label className="flex cursor-pointer items-start gap-2.5">
            <input
              type="checkbox"
              checked={canComment}
              onChange={(e) => setCanComment(e.target.checked)}
              className="garh-focus-ring mt-0.5 h-4 w-4 rounded border-line-strong accent-[rgb(var(--garh-brand))]"
            />
            <span>
              <span className="block text-sm text-ink">Let them pin comments</span>
              <span className="block text-xs text-ink-muted">
                Comments appear in your project. They still cannot change anything.
              </span>
            </span>
          </label>

          <p className="flex items-start gap-1.5 rounded-md bg-surface-muted p-2.5 text-2xs leading-4 text-ink-muted">
            <Icon name="lock" size={13} className="mt-px shrink-0" />
            <span>
              The link is a one-off random token. We store only a hash of it, so it cannot be
              recovered later — create a new one if you lose it. Creating and revoking links is
              recorded in your firm&apos;s activity log.
            </span>
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="flex items-center gap-2">
            <Input readOnly value={shareUrl} aria-label="Share link" className="font-mono text-xs" />
            <Button variant="secondary" iconLeft="copy" onClick={() => void handleCopy()}>
              Copy
            </Button>
          </div>

          {expiresAt === undefined ? null : (
            <p className="text-xs text-ink-muted garh-nums">
              Works until {formatIndianDate(expiresAt)}.
            </p>
          )}

          <div>
            <p className="mb-1.5 text-xs font-medium text-ink-muted">Message they will get</p>
            <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-md border border-line bg-surface-muted p-3 text-xs leading-5 text-ink">
              {message}
            </pre>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <WhatsAppShareButton message={message} />
            <Button variant="secondary" iconLeft="external-link" onClick={() => window.open(shareUrl, '_blank', 'noopener,noreferrer')}>
              Preview as client
            </Button>
            <Badge tone="neutral">Read-only</Badge>
          </div>
        </div>
      )}
    </Dialog>
  );
}

export interface WhatsAppShareButtonProps {
  /** The preformatted message, usually from `buildShareMessage`. */
  message: string;
  /** Optional recipient. Left empty on purpose — see the file header. */
  phone?: string | undefined;
  size?: 'sm' | 'md' | 'lg' | undefined;
  className?: string | undefined;
}

/**
 * Opens WhatsApp with the message prefilled. It is a link, not a fetch: nothing
 * is sent until the user presses send inside WhatsApp, which is the only place
 * that decision belongs.
 */
export function WhatsAppShareButton({
  message,
  phone,
  size = 'md',
  className,
}: WhatsAppShareButtonProps): JSX.Element {
  return (
    <LinkButton
      href={whatsappShareUrl(message, phone)}
      target="_blank"
      rel="noopener noreferrer"
      variant="primary"
      size={size}
      iconLeft="message"
      className={className}
    >
      Share on WhatsApp
    </LinkButton>
  );
}
