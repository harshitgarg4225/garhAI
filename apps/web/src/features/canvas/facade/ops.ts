/**
 * ops.ts — op builders for the facade feature. Golden rule 1: these BUILD
 * ops; the model store dispatches them; nothing here touches state.
 *
 * `facade.apply_kit` (op 27) CARRIES the generated components in its payload —
 * the fold never re-runs the generator, so an op log replays identically even
 * if the generator's policy constants change in a later release. That is why
 * `applyKitOp` runs the generator here, at build time, and why the payload is
 * the generator's output verbatim.
 *
 * `facade.edit_component` (op 28) is an RFC 7386 merge patch. The builders
 * assert every `*Mm` number is a safe integer BEFORE the op leaves this module
 * — op validation would reject a float anyway, but a throw here names the
 * caller that produced it instead of a toast naming the field.
 */

import {
  assertIntMm,
  type FacadeComponentId,
  type HouseModel,
  type JsonObject,
  type JsonValue,
  type Op,
} from '@garh/model';

import type { FacadeKitDef } from './types';
import { generateFacadeComponents } from './generator';

/**
 * Build the "apply this kit" op: generate components for the CURRENT model and
 * wrap them in op 27. Also the "regenerate" op — same kit, same seed, fresh
 * walk of the (possibly edited) plan.
 */
export function applyKitOp(
  house: HouseModel,
  kit: FacadeKitDef,
  seed: number,
  colorwayId: string | null,
): Op {
  if (!Number.isSafeInteger(seed) || seed < 0) {
    throw new Error(`Facade seed must be a non-negative integer, got ${String(seed)}`);
  }
  return {
    type: 'facade.apply_kit',
    payload: {
      kitId: kit.id,
      seed,
      colorwayId,
      components: generateFacadeComponents(house, kit, seed, { colorwayId }),
    },
  };
}

/** Clear the facade sub-model entirely (op 27's documented null form). */
export function clearFacadeOp(): Op {
  return {
    type: 'facade.apply_kit',
    payload: { kitId: null, seed: 0, colorwayId: null, components: [] },
  };
}

/**
 * Patch one component's params (op 28). Walks the patch and asserts every
 * number is a safe integer — `params` lives inside the hashed document, and
 * `canonicalJson` refuses floats.
 */
export function editComponentOp(componentId: FacadeComponentId, patch: JsonObject): Op {
  assertIntegralJson(patch, 'facade.edit_component.patch');
  return {
    type: 'facade.edit_component',
    payload: { componentId, patch },
  };
}

function assertIntegralJson(value: JsonValue, path: string): void {
  if (typeof value === 'number') {
    assertIntMm(value, path);
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item, i) => {
      assertIntegralJson(item, `${path}[${String(i)}]`);
    });
    return;
  }
  if (typeof value === 'object' && value !== null) {
    for (const key of Object.keys(value)) {
      const inner = (value as JsonObject)[key];
      if (inner !== undefined) assertIntegralJson(inner, `${path}.${key}`);
    }
  }
}
