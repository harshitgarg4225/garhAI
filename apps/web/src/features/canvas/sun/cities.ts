/**
 * cities.ts — the lat/long the sun widget uses, keyed by the same rule-pack
 * ids the plot's regulatory profile carries (`plot.regProfile.cityPack`:
 * 'blr' | 'ncr' | 'hyd' — see `packages/model/src/model.ts` RegProfile).
 *
 * SOURCE OF THE COORDINATES: the commonly published city-centre points, as
 * carried by OpenStreetMap's node for each city (and identical to 4 dp on
 * Wikipedia's city infoboxes):
 *   Bengaluru  12.9716 N, 77.5946 E   (Vidhana Soudha area)
 *   New Delhi  28.6139 N, 77.2090 E   (Connaught Place / India Gate area)
 *   Hyderabad  17.3850 N, 78.4867 E   (Hussain Sagar area)
 *
 * A centroid is *good enough by construction* for a shadow study: moving the
 * observer 30 km moves the sun by under 0.3° — far less than the hour-to-hour
 * scrub step — so a per-plot geocode would be precision theatre. The honest
 * part is saying which point we used, which is what this file is.
 */

export interface CityCentroid {
  /** Rule-pack id — the key `plot.regProfile.cityPack` carries. */
  readonly packId: string;
  readonly name: string;
  readonly latDeg: number;
  readonly lonDeg: number;
}

export const CITY_CENTROIDS: readonly CityCentroid[] = [
  { packId: 'blr', name: 'Bengaluru', latDeg: 12.9716, lonDeg: 77.5946 },
  { packId: 'ncr', name: 'Delhi NCR', latDeg: 28.6139, lonDeg: 77.209 },
  { packId: 'hyd', name: 'Hyderabad', latDeg: 17.385, lonDeg: 78.4867 },
];

/**
 * The default when a project has no city pack yet. Bengaluru — the seeded demo
 * project's city. Callers must SAY they defaulted (§15: assumptions are
 * visible); `SunPanel` renders the "assumed" hint whenever this fires.
 */
export const DEFAULT_CITY: CityCentroid = CITY_CENTROIDS[0] as CityCentroid;

/** Centroid for a rule-pack id, or null when the pack is unknown/unset. */
export function cityForPack(cityPack: string | null | undefined): CityCentroid | null {
  if (cityPack === null || cityPack === undefined) return null;
  return CITY_CENTROIDS.find((c) => c.packId === cityPack) ?? null;
}
