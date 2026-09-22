/**
 * A picture arrives with the name its client gave it.
 *
 * The board labels every card, and the server has always preferred the uploaded
 * file's name over "Reference 3" (`ReferenceRepository.add`). But the web app posts
 * the raw image bytes, not multipart, so the name had nowhere to travel: the
 * fallback was dead code and every board in the product read "Reference 1 …
 * Reference 8". Found by uploading three named pictures in a browser and reading
 * the board back — and it is also what the pre-render review names its questions
 * after, so "What should Reference 6 contribute?" was the question an architect got
 * about a photo they could not identify.
 *
 * A name is not an inference. The product still reads nothing OUT of the picture or
 * its filename — no scope, no intent — and the label never reaches a provider
 * (`build_prompt` is assembled from `why` and `ignore` alone). It is the one thing
 * the architect already chose, shown back to them in an editable field.
 *
 * The encoding is the part worth testing. A header value is bytes: a client in
 * Bengaluru sending `ಅಡುಗೆಮನೆ.jpg` would make `fetch` throw on a raw value and take
 * the upload down with it.
 */

import { describe, expect, it } from 'vitest';

import { referenceFilenameHeader } from './api';

/** A `File` is a `Blob` with a name; a paste or a canvas capture is not. */
function file(name: string): Blob {
  return new File([new Uint8Array([1, 2, 3])], name, { type: 'image/jpeg' });
}

describe('referenceFilenameHeader', () => {
  it('sends the name the architect sees on their own disk', () => {
    expect(referenceFilenameHeader(file('kitchen-tiles.jpg'))).toBe('kitchen-tiles.jpg');
  });

  it('survives a name that is not ASCII, instead of throwing on the way out', () => {
    const encoded = referenceFilenameHeader(file('ಅಡುಗೆಮನೆ.jpg'));
    expect(encoded).not.toBeNull();
    // The value must be header-safe: every byte ASCII, no spaces, no controls.
    expect(encoded ?? '').toMatch(/^[\x21-\x7e]+$/);
    expect(decodeURIComponent(encoded ?? '')).toBe('ಅಡುಗೆಮನೆ.jpg');
  });

  it('never sends a path, only a name', () => {
    expect(decodeURIComponent(referenceFilenameHeader(file('../../etc/passwd')) ?? '')).toBe(
      'passwd',
    );
    expect(
      decodeURIComponent(referenceFilenameHeader(file('C:\\Users\\asha\\kitchen.jpg')) ?? ''),
    ).toBe('kitchen.jpg');
  });

  it('strips control characters, which must never reach a header', () => {
    // A bare CR/LF in a header value is request splitting. `fetch` would reject
    // it, but the value is dropped here so the upload still succeeds.
    const encoded = referenceFilenameHeader(file('kitchen\r\nX-Evil: 1.jpg')) ?? '';
    expect(decodeURIComponent(encoded)).toBe('kitchenX-Evil: 1.jpg');
    expect(encoded).not.toContain('%0d');
    expect(encoded).not.toContain('%0a');
  });

  it('NEGATIVE CONTROL: a blob with no name invents none', () => {
    // A pasted image has no filename. "Reference 3" is the honest label there;
    // a fabricated "image.jpg" on every card would be worse than a counter.
    expect(referenceFilenameHeader(new Blob([new Uint8Array([1])], { type: 'image/png' }))).toBe(
      null,
    );
    expect(referenceFilenameHeader(file(''))).toBe(null);
    expect(referenceFilenameHeader(file('   '))).toBe(null);
  });
});
