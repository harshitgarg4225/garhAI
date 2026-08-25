/**
 * whatsapp.ts — §15's "Share on WhatsApp": a `wa.me` deep link with a
 * preformatted message. No SDK, no tracking — the deep link is the feature.
 */

/** `https://wa.me/?text=…` — WhatsApp opens with the message pre-filled. */
export function waShareUrl(message: string): string {
  return `https://wa.me/?text=${encodeURIComponent(message)}`;
}

/** The render-share message. Plain, warm, no jargon (§15 tone). */
export function renderShareMessage(projectName: string, url: string): string {
  return `${projectName} — new renders from Garh AI.\n${url}\n(The link is valid for 10 minutes; ask me for a fresh one any time.)`;
}
