/**
 * Client-generated identifiers. Three kinds, three different reasons.
 *
 * | id              | shape                | why                                          |
 * |-----------------|----------------------|----------------------------------------------|
 * | `clientOpId`    | `op_<ULID>`          | idempotency unit of the op log (§11)          |
 * | `groupId`       | UUID v4              | undo/redo batching (§4); the API types it uuid|
 * | `Idempotency-Key`| UUID v4             | replay-safety for non-op POSTs (§11)          |
 *
 * The two shapes are not interchangeable, and the mismatch is deliberate rather
 * than accidental: `garh_api.schemas.ops.OpIn.group_id` is a `uuid.UUID`, while
 * `client_op_id` is a bounded string documented as "a ULID in practice". Sending
 * a `group_<ULID>` where a uuid is expected is a 422 that would look like a
 * mysterious validation failure on an otherwise-correct edit.
 *
 * `clientOpId` routes through the model core's `newId('op')` so that
 * `setUlidFactory()` makes an entire store test deterministic — which is what
 * lets the optimistic-queue tests assert on exact request bodies.
 */

import { newId } from '@garh/model';

/** Idempotency key for the op log. One per op, always sent (§11). */
export function newClientOpId(): string {
  return newId('op');
}

/**
 * A UUID v4. Prefers the platform generator; falls back to `getRandomValues`
 * for older WebViews, which still gives 122 bits of CSPRNG entropy.
 *
 * There is no `Math.random()` path. A collision here means one client's edit is
 * silently deduplicated against another's, and "probably unique" is not a
 * property worth having in an op log.
 */
export function newUuid(): string {
  const c: Crypto | undefined = typeof globalThis.crypto === 'undefined' ? undefined : globalThis.crypto;
  if (c && typeof c.randomUUID === 'function') return c.randomUUID();
  if (c && typeof c.getRandomValues === 'function') {
    const bytes = c.getRandomValues(new Uint8Array(16));
    // RFC 4122 §4.4: set version (4) and variant (10xx).
    bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
    bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
    const hex: string[] = [];
    for (let i = 0; i < 16; i++) hex.push((bytes[i] ?? 0).toString(16).padStart(2, '0'));
    return [
      hex.slice(0, 4).join(''),
      hex.slice(4, 6).join(''),
      hex.slice(6, 8).join(''),
      hex.slice(8, 10).join(''),
      hex.slice(10, 16).join(''),
    ].join('-');
  }
  throw new Error(
    'This browser has no Web Crypto. Garh AI needs it to generate edit ids safely — ' +
      'please use a current version of Chrome, Edge, Firefox or Safari.',
  );
}

/** Undo/redo batch id (§4). A uuid, because that is what the op endpoint types. */
export function newGroupId(): string {
  return newUuid();
}

/** `Idempotency-Key` header value for a non-op mutation (§11). */
export function newIdempotencyKey(): string {
  return newUuid();
}
