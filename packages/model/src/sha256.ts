/**
 * Minimal, dependency-free SHA-256.
 *
 * WHY hand-rolled: `stateHash()` must be
 *   (a) synchronous — it is called inside `fold()` and in vitest perf tests,
 *   (b) identical in Node and in the browser bundle (no `node:crypto` import,
 *       which would break the Vite build for `apps/web`),
 *   (c) byte-for-byte reproducible against the Python mirror (`hashlib.sha256`).
 * Web Crypto's `crypto.subtle.digest` is async, so it cannot be used here.
 *
 * This is FIPS 180-4 SHA-256, no tricks. Verified against the standard test
 * vectors in `sha256.test.ts`.
 */

const K: readonly number[] = [
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

const HEX = '0123456789abcdef';

function rotr(x: number, n: number): number {
  return ((x >>> n) | (x << (32 - n))) >>> 0;
}

/** SHA-256 of raw bytes. Returns lowercase hex, 64 chars. */
export function sha256Bytes(input: Uint8Array): string {
  const bitLen = input.length * 8;
  // padded length: message + 0x80 + zeros + 8-byte big-endian bit length, multiple of 64
  const paddedLen = Math.ceil((input.length + 9) / 64) * 64;
  const msg = new Uint8Array(paddedLen);
  msg.set(input, 0);
  msg[input.length] = 0x80;
  // bit length as 64-bit big endian; JS numbers are safe up to 2^53 bits of message.
  const hi = Math.floor(bitLen / 0x100000000);
  const lo = bitLen >>> 0;
  msg[paddedLen - 8] = (hi >>> 24) & 0xff;
  msg[paddedLen - 7] = (hi >>> 16) & 0xff;
  msg[paddedLen - 6] = (hi >>> 8) & 0xff;
  msg[paddedLen - 5] = hi & 0xff;
  msg[paddedLen - 4] = (lo >>> 24) & 0xff;
  msg[paddedLen - 3] = (lo >>> 16) & 0xff;
  msg[paddedLen - 2] = (lo >>> 8) & 0xff;
  msg[paddedLen - 1] = lo & 0xff;

  let h0 = 0x6a09e667;
  let h1 = 0xbb67ae85;
  let h2 = 0x3c6ef372;
  let h3 = 0xa54ff53a;
  let h4 = 0x510e527f;
  let h5 = 0x9b05688c;
  let h6 = 0x1f83d9ab;
  let h7 = 0x5be0cd19;

  const w = new Uint32Array(64);

  for (let off = 0; off < paddedLen; off += 64) {
    for (let i = 0; i < 16; i++) {
      const j = off + i * 4;
      w[i] = ((msg[j]! << 24) | (msg[j + 1]! << 16) | (msg[j + 2]! << 8) | msg[j + 3]!) >>> 0;
    }
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(w[i - 15]!, 7) ^ rotr(w[i - 15]!, 18) ^ (w[i - 15]! >>> 3);
      const s1 = rotr(w[i - 2]!, 17) ^ rotr(w[i - 2]!, 19) ^ (w[i - 2]! >>> 10);
      w[i] = (w[i - 16]! + s0 + w[i - 7]! + s1) >>> 0;
    }

    let a = h0;
    let b = h1;
    let c = h2;
    let d = h3;
    let e = h4;
    let f = h5;
    let g = h6;
    let h = h7;

    for (let i = 0; i < 64; i++) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const temp1 = (h + S1 + ch + K[i]! + w[i]!) >>> 0;
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (S0 + maj) >>> 0;

      h = g;
      g = f;
      f = e;
      e = (d + temp1) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (temp1 + temp2) >>> 0;
    }

    h0 = (h0 + a) >>> 0;
    h1 = (h1 + b) >>> 0;
    h2 = (h2 + c) >>> 0;
    h3 = (h3 + d) >>> 0;
    h4 = (h4 + e) >>> 0;
    h5 = (h5 + f) >>> 0;
    h6 = (h6 + g) >>> 0;
    h7 = (h7 + h) >>> 0;
  }

  return (
    hex32(h0) + hex32(h1) + hex32(h2) + hex32(h3) + hex32(h4) + hex32(h5) + hex32(h6) + hex32(h7)
  );
}

function hex32(x: number): string {
  let out = '';
  for (let shift = 28; shift >= 0; shift -= 4) {
    out += HEX[(x >>> shift) & 0xf];
  }
  return out;
}

/**
 * UTF-8 encode without depending on `TextEncoder` being present (it is in Node
 * 20 and every target browser, but keeping this local makes the hash provably
 * identical in every runtime and mirrors `s.encode('utf-8')` in Python).
 *
 * Lone surrogates are encoded as U+FFFD, matching Python's `errors='replace'`
 * behaviour; `canonicalJson` rejects lone surrogates before we get here.
 */
export function utf8Bytes(s: string): Uint8Array {
  const out: number[] = [];
  for (let i = 0; i < s.length; i++) {
    let cp = s.charCodeAt(i);
    if (cp >= 0xd800 && cp <= 0xdbff) {
      const next = i + 1 < s.length ? s.charCodeAt(i + 1) : 0;
      if (next >= 0xdc00 && next <= 0xdfff) {
        cp = (cp - 0xd800) * 0x400 + (next - 0xdc00) + 0x10000;
        i++;
      } else {
        cp = 0xfffd;
      }
    } else if (cp >= 0xdc00 && cp <= 0xdfff) {
      cp = 0xfffd;
    }
    if (cp < 0x80) {
      out.push(cp);
    } else if (cp < 0x800) {
      out.push(0xc0 | (cp >> 6), 0x80 | (cp & 0x3f));
    } else if (cp < 0x10000) {
      out.push(0xe0 | (cp >> 12), 0x80 | ((cp >> 6) & 0x3f), 0x80 | (cp & 0x3f));
    } else {
      out.push(
        0xf0 | (cp >> 18),
        0x80 | ((cp >> 12) & 0x3f),
        0x80 | ((cp >> 6) & 0x3f),
        0x80 | (cp & 0x3f),
      );
    }
  }
  return new Uint8Array(out);
}

/** SHA-256 of a UTF-8 string. Returns lowercase hex, 64 chars. */
export function sha256Utf8(s: string): string {
  return sha256Bytes(utf8Bytes(s));
}
