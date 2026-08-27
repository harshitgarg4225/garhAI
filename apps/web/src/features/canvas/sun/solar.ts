/**
 * solar.ts — the NOAA solar position algorithm. Zero dependencies, zero three,
 * zero React: (UTC instant, latitude, longitude) → azimuth + elevation.
 *
 * SOURCE. These are the equations behind the NOAA Global Monitoring Laboratory
 * solar calculator (gml.noaa.gov/grad/solcalc — the published
 * `NOAA_Solar_Calculations` spreadsheet), which are themselves the truncated
 * VSOP87 series from Meeus, *Astronomical Algorithms*, ch. 25. Playbook §8
 * asks for exactly this: "implement NOAA solar position algorithm — ~40 lines,
 * no dependency needed". Stated accuracy of the truncated series is ±0.01° of
 * declination for years 1950–2050, which is three orders of magnitude finer
 * than a shadow study needs.
 *
 * CONVENTIONS (the same ones the NOAA calculator prints):
 *   - azimuth: degrees CLOCKWISE from TRUE NORTH, 0..360. 90 = east.
 *   - elevation: degrees above the horizon. Negative = sun below horizon.
 *   - longitude EAST-positive (Bengaluru is +77.59), latitude north-positive.
 *
 * Everything here is float degrees on purpose. Nothing in this file is
 * geometry and nothing becomes an op payload — the sun is a light, not a wall
 * (see the boundary note in `core/coords.ts`). The one integer-adjacent rule
 * that does apply: the *inputs* (date, minutes, city) come from the sun store,
 * which stores them as integers.
 */

const DEG = Math.PI / 180;

/** What the algorithm returns. All float degrees / minutes. */
export interface SolarPosition {
  /** Degrees clockwise from true north, [0, 360). */
  readonly azimuthDeg: number;
  /** Geometric elevation above the horizon; negative below it. */
  readonly elevationDeg: number;
  /** Elevation with NOAA's atmospheric-refraction correction applied. */
  readonly apparentElevationDeg: number;
  /** Solar declination — exposed because the specs pin it at the solstices. */
  readonly declinationDeg: number;
  /** Equation of time, minutes — exposed for the same reason. */
  readonly eqTimeMinutes: number;
}

/**
 * NOAA atmospheric refraction correction, in degrees, for a geometric
 * elevation `h` (degrees). Piecewise per the NOAA calculator: ~0.56° at the
 * horizon, ~0.016° at 45°, zero above 85°.
 */
export function refractionDeg(elevationDeg: number): number {
  if (elevationDeg > 85) return 0;
  const t = Math.tan(elevationDeg * DEG);
  let seconds: number;
  if (elevationDeg > 5) {
    seconds = 58.1 / t - 0.07 / t ** 3 + 0.000086 / t ** 5;
  } else if (elevationDeg > -0.575) {
    const h = elevationDeg;
    seconds = 1735 + h * (-518.2 + h * (103.4 + h * (-12.79 + 0.711 * h)));
  } else {
    seconds = -20.774 / t;
  }
  return seconds / 3600;
}

/**
 * Solar azimuth + elevation at a UTC instant, for an observer at
 * (`latDeg` N, `lonDeg` E). The ~40 lines §8 promised.
 */
export function solarPosition(utcMs: number, latDeg: number, lonDeg: number): SolarPosition {
  // Julian day / century from the Unix epoch (which is JD 2440587.5).
  const jd = utcMs / 86_400_000 + 2_440_587.5;
  const t = (jd - 2_451_545) / 36_525;

  // Geometric mean longitude / anomaly of the sun; orbital eccentricity.
  const l0 = (((280.46646 + t * (36000.76983 + 0.0003032 * t)) % 360) + 360) % 360;
  const m = 357.52911 + t * (35999.05029 - 0.0001537 * t);
  const e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t);

  // Equation of centre → true → apparent longitude (nutation + aberration).
  const c =
    Math.sin(m * DEG) * (1.914602 - t * (0.004817 + 0.000014 * t)) +
    Math.sin(2 * m * DEG) * (0.019993 - 0.000101 * t) +
    Math.sin(3 * m * DEG) * 0.000289;
  const omega = 125.04 - 1934.136 * t;
  const lambda = l0 + c - 0.00569 - 0.00478 * Math.sin(omega * DEG);

  // Obliquity of the ecliptic, corrected, → declination.
  const eps0 = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60;
  const eps = eps0 + 0.00256 * Math.cos(omega * DEG);
  const declinationDeg = Math.asin(Math.sin(eps * DEG) * Math.sin(lambda * DEG)) / DEG;

  // Equation of time (minutes), then true solar time → hour angle.
  const y = Math.tan((eps * DEG) / 2) ** 2;
  const eqTimeMinutes =
    (4 / DEG) *
    (y * Math.sin(2 * l0 * DEG) -
      2 * e * Math.sin(m * DEG) +
      4 * e * y * Math.sin(m * DEG) * Math.cos(2 * l0 * DEG) -
      0.5 * y * y * Math.sin(4 * l0 * DEG) -
      1.25 * e * e * Math.sin(2 * m * DEG));
  const minutesUtc = (((utcMs / 60_000) % 1440) + 1440) % 1440;
  const trueSolarMinutes = (((minutesUtc + eqTimeMinutes + 4 * lonDeg) % 1440) + 1440) % 1440;
  let haDeg = trueSolarMinutes / 4 - 180;
  if (haDeg < -180) haDeg += 360;

  // Zenith → elevation; azimuth from the atan2 form (no quadrant branches).
  const cosZen = Math.min(
    1,
    Math.max(
      -1,
      Math.sin(latDeg * DEG) * Math.sin(declinationDeg * DEG) +
        Math.cos(latDeg * DEG) * Math.cos(declinationDeg * DEG) * Math.cos(haDeg * DEG),
    ),
  );
  const elevationDeg = 90 - Math.acos(cosZen) / DEG;
  const azimuthDeg =
    (((Math.atan2(
      Math.sin(haDeg * DEG),
      Math.cos(haDeg * DEG) * Math.sin(latDeg * DEG) -
        Math.tan(declinationDeg * DEG) * Math.cos(latDeg * DEG),
    ) /
      DEG +
      180) %
      360) +
      360) %
    360;

  return {
    azimuthDeg,
    elevationDeg,
    apparentElevationDeg: elevationDeg + refractionDeg(elevationDeg),
    declinationDeg,
    eqTimeMinutes,
  };
}

// ---------------------------------------------------------------------------
// IST — the only timezone this product's three cities live in
// ---------------------------------------------------------------------------

/** Indian Standard Time is UTC+5:30, year-round. India observes no DST. */
export const IST_UTC_OFFSET_MINUTES = 330;

/** A calendar date, integer fields. Month is 1-based (1 = January). */
export interface CalendarDate {
  readonly year: number;
  readonly month: number;
  readonly day: number;
}

/**
 * Days since the Unix epoch for a proleptic-Gregorian civil date.
 * Howard Hinnant's `days_from_civil` — branch-free, exact over the whole
 * int range, and independent of the host `Date`'s timezone parsing quirks.
 */
export function daysFromCivil(date: CalendarDate): number {
  const y = date.year - (date.month <= 2 ? 1 : 0);
  const era = Math.floor(y / 400);
  const yoe = y - era * 400;
  const doy = Math.floor((153 * (date.month + (date.month > 2 ? -3 : 9)) + 2) / 5) + date.day - 1;
  const doe = yoe * 365 + Math.floor(yoe / 4) - Math.floor(yoe / 100) + doy;
  return era * 146_097 + doe - 719_468;
}

/** IST wall-clock (date + minutes past midnight) → UTC epoch milliseconds. */
export function istToUtcMs(date: CalendarDate, minutesOfDay: number): number {
  return (daysFromCivil(date) * 1440 + minutesOfDay - IST_UTC_OFFSET_MINUTES) * 60_000;
}

/** UTC epoch milliseconds → IST wall-clock, the inverse of {@link istToUtcMs}. */
export function utcMsToIst(utcMs: number): { date: CalendarDate; minutesOfDay: number } {
  const istMinutes = Math.floor(utcMs / 60_000) + IST_UTC_OFFSET_MINUTES;
  const days = Math.floor(istMinutes / 1440);
  const minutesOfDay = istMinutes - days * 1440;
  // civil_from_days, the inverse of daysFromCivil.
  const z = days + 719_468;
  const era = Math.floor(z / 146_097);
  const doe = z - era * 146_097;
  const yoe = Math.floor(
    (doe - Math.floor(doe / 1460) + Math.floor(doe / 36_524) - Math.floor(doe / 146_096)) / 365,
  );
  const y = yoe + era * 400;
  const doy = doe - (365 * yoe + Math.floor(yoe / 4) - Math.floor(yoe / 100));
  const mp = Math.floor((5 * doy + 2) / 153);
  const day = doy - Math.floor((153 * mp + 2) / 5) + 1;
  const month = mp < 10 ? mp + 3 : mp - 9;
  return { date: { year: y + (month <= 2 ? 1 : 0), month, day }, minutesOfDay };
}
