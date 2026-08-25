/**
 * Share-link helpers (§15 "Share on WhatsApp", §F10 client share links).
 *
 * WhatsApp is how an Indian architect actually sends a plan to a client, so it
 * gets a first-class deep link rather than a generic "copy URL". `wa.me` is a
 * plain https URL — no SDK, no third-party script, nothing for the CSP to
 * allow — which is exactly why it is the right integration.
 */

import { appUrlFor, env } from './env';

/** Route a share token resolves to in this app. */
export function shareViewerPath(token: string): string {
  return `/share/${encodeURIComponent(token)}`;
}

/** Absolute, sendable URL for a share token. */
export function shareViewerUrl(token: string): string {
  return appUrlFor(shareViewerPath(token));
}

export interface WhatsAppMessage {
  /** The link being shared. */
  readonly url: string;
  /** Project name, so the message says what it is. */
  readonly projectName?: string;
  /** Optional recipient in international form (`919876543210`). */
  readonly phone?: string;
  /** Override the generated body entirely. */
  readonly text?: string;
}

/**
 * Build a `wa.me` deep link.
 *
 * The default body is deliberately plain and warm (§15 tone) and never claims
 * the drawings are approved — Garh AI is advisory, and a forwarded message is
 * exactly where an over-claim would do damage.
 */
export function whatsappShareUrl(input: WhatsAppMessage): string {
  const name = input.projectName?.trim();
  const body =
    input.text ??
    (name
      ? `Here's the design for ${name}, from ${env.appName}. You can view the plans and leave comments here: ${input.url}`
      : `Here's the design, from ${env.appName}. You can view the plans and leave comments here: ${input.url}`);

  const digits = (input.phone ?? '').replace(/\D/g, '');
  const base = digits ? `https://wa.me/${digits}` : 'https://wa.me/';
  return `${base}?text=${encodeURIComponent(body)}`;
}

/**
 * Copy text to the clipboard, resolving to whether it worked.
 *
 * The async Clipboard API needs a secure context and a user gesture; when it is
 * unavailable this returns `false` rather than throwing, so the caller can show
 * the URL in a selectable field instead of an error. Never falls back to the
 * deprecated `document.execCommand` hack — it fails silently in enough browsers
 * to be worse than an honest "copy this yourself".
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (typeof navigator === 'undefined' || !navigator.clipboard) return false;
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
