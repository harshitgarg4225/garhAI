/**
 * WhatsApp share links (§15).
 *
 * Two properties matter beyond "it builds a URL":
 *  - the message must survive encoding intact, newlines included, because a
 *    broken deep link fails silently — WhatsApp opens with an empty composer
 *    and the architect never learns why;
 *  - no recipient phone number appears unless the caller passed one. The
 *    default share flow lets WhatsApp's own picker choose the contact, which is
 *    what keeps a client's number out of our URLs.
 */

import { describe, expect, it } from 'vitest';

import { buildShareMessage, whatsappShareUrl } from './share';

describe('whatsappShareUrl', () => {
  it('builds a recipient-less link by default', () => {
    expect(whatsappShareUrl('hello')).toBe('https://wa.me/?text=hello');
  });

  it('percent-encodes spaces, newlines and reserved characters', () => {
    const url = whatsappShareUrl('a b\nc&d=e');
    expect(url).toBe('https://wa.me/?text=a%20b%0Ac%26d%3De');
    expect(decodeURIComponent(url.split('text=')[1] ?? '')).toBe('a b\nc&d=e');
  });

  it('keeps a share URL intact through encode/decode', () => {
    const link = 'https://app.garh.ai/share/abc123?v=7';
    const url = whatsappShareUrl(`See it here: ${link}`);
    expect(decodeURIComponent(url.split('text=')[1] ?? '')).toContain(link);
  });

  it('strips separators from a supplied phone number', () => {
    expect(whatsappShareUrl('hi', '+91 98765 43210')).toBe('https://wa.me/919876543210?text=hi');
  });

  it('falls back to the picker when the phone has no digits', () => {
    expect(whatsappShareUrl('hi', '')).toBe('https://wa.me/?text=hi');
    expect(whatsappShareUrl('hi', '--')).toBe('https://wa.me/?text=hi');
  });
});

describe('buildShareMessage', () => {
  const base = { projectName: 'Sharma Residence', url: 'https://app.garh.ai/share/tok' };

  it('leads with the project name and always includes the link', () => {
    const msg = buildShareMessage(base);
    expect(msg.startsWith('Sharma Residence — design preview')).toBe(true);
    expect(msg).toContain(base.url);
  });

  it('joins the plot and configuration facts on one line', () => {
    const msg = buildShareMessage({
      ...base,
      plotSummary: '1,200.0 sq ft · 133 gaj',
      configuration: 'G+1 · 3 BHK',
    });
    expect(msg).toContain('1,200.0 sq ft · 133 gaj · G+1 · 3 BHK');
  });

  it('omits the facts line entirely when there are no facts', () => {
    const msg = buildShareMessage(base);
    expect(msg.split('\n')[1]).toBe('');
  });

  it('states the expiry in DD-MM-YYYY when one is given', () => {
    const msg = buildShareMessage({ ...base, expiresOn: '14-08-2026' });
    expect(msg).toContain('This link works until 14-08-2026.');
  });

  it('signs off with the firm when one is given, and not otherwise', () => {
    const signed = buildShareMessage({ ...base, firmName: 'Studio Demo' });
    expect(signed.split('\n').at(-1)).toBe('— Studio Demo');
    // Without a firm the message must end on the link, not on a dangling dash.
    // (The title line contains an em dash of its own, so this checks the last
    // line rather than the whole string.)
    expect(buildShareMessage(base).split('\n').at(-1)).toBe(base.url);
  });

  it('produces a message that round-trips through the deep link', () => {
    const msg = buildShareMessage({ ...base, plotSummary: '1,200.0 sq ft · 133 gaj' });
    const url = whatsappShareUrl(msg);
    expect(decodeURIComponent(url.split('text=')[1] ?? '')).toBe(msg);
  });
});
