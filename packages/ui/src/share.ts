/**
 * share.ts — WhatsApp deep links and the copy that goes in them.
 *
 * §15: "'Share on WhatsApp' on renders/share links (wa.me deep link with
 * preformatted message)". WhatsApp is how Indian architects actually send work
 * to clients, so the share surface is built around it rather than email.
 *
 * `wa.me` is the officially documented click-to-chat endpoint and needs no SDK,
 * no app id and no user consent dialog — it is a plain link. We never prefill a
 * phone number: the user picks the recipient in WhatsApp's own UI, which keeps
 * the client's number out of our URL bar and out of any analytics.
 */

/** Build a wa.me URL with a preformatted message. */
export function whatsappShareUrl(message: string, phone?: string): string {
  const text = encodeURIComponent(message);
  const digits = phone === undefined ? '' : phone.replace(/\D+/g, '');
  return digits === '' ? `https://wa.me/?text=${text}` : `https://wa.me/${digits}?text=${text}`;
}

export interface ShareMessageInput {
  projectName: string;
  /** The signed, scoped share URL. */
  url: string;
  /** "1,200.0 sq ft · 133 gaj" — from `formatPlotArea`. */
  plotSummary?: string | undefined;
  /** "G+1 · 3 BHK". */
  configuration?: string | undefined;
  /** Firm name for the sign-off. */
  firmName?: string | undefined;
  /** DD-MM-YYYY link expiry, from `formatIndianDate`. */
  expiresOn?: string | undefined;
}

/**
 * The message an architect sends a client. Deliberately plain and short —
 * WhatsApp truncates previews, and anything longer reads as marketing.
 * Line breaks are real newlines; `encodeURIComponent` handles them.
 */
export function buildShareMessage(input: ShareMessageInput): string {
  const lines: string[] = [];
  lines.push(`${input.projectName} — design preview`);
  const facts = [input.plotSummary, input.configuration].filter(
    (x): x is string => x !== undefined && x !== '',
  );
  if (facts.length > 0) lines.push(facts.join(' · '));
  lines.push('');
  lines.push('View the plans, 3D and renders here:');
  lines.push(input.url);
  if (input.expiresOn !== undefined) {
    lines.push('');
    lines.push(`This link works until ${input.expiresOn}.`);
  }
  if (input.firmName !== undefined) {
    lines.push('');
    lines.push(`— ${input.firmName}`);
  }
  return lines.join('\n');
}

/**
 * Copy text to the clipboard, resolving to false rather than throwing when the
 * page has no clipboard permission (http origins, older Safari). The caller
 * shows a "Copy didn't work — here's the link" fallback instead of an error.
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard !== undefined) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to the failure path below
  }
  return false;
}
