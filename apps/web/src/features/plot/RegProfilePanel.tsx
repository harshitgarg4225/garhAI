/**
 * RegProfilePanel — city preset + the resolved regulatory numbers for THIS
 * plot, every one an editable override chip with its citation and confidence.
 *
 * Two locked product decisions are load-bearing here:
 *   - golden rule 4 (assumptions visible): every value shows WHERE it came
 *     from (rule id, bye-law table, pack) and how much to trust it — a
 *     `"seed"` value is styled as provisional and says so out loud;
 *   - golden rule 5 (compliance informs): overrides are always allowed. They
 *     dispatch `plot.set_reg_profile`, which the server audit-logs (§13).
 */

import { formatLength, formatPlotArea, polygonAreaMm2, tryParseLengthMm } from '@garh/model';
import { AssumptionChip, Button, Chip, PanelSection, SelectField, SkeletonText } from '@garh/ui';

import { frontEdgeIndex } from './geometry';
import {
  CITY_PACK_OPTIONS,
  REG_VALUE_KEYS,
  REG_VALUE_META,
  buildRegFacts,
  cityPackFromStored,
  cityPackToStored,
  formatRegValue,
  parseRegScalar,
  resolveRegValues,
  rulepackDocSchema,
  withValueOverride,
  type RegValueKey,
  type ResolvedRegProfile,
} from './rules';
import {
  useModelReady,
  usePlotActions,
  usePlotDoc,
  useRulepack,
  useRulepackList,
  useUnitsDisplay,
} from './usePlot';

const EMPTY_PACK = rulepackDocSchema.parse({ pack: 'custom', rules: [] });

export interface RegProfilePanelProps {
  className?: string | undefined;
}

export function RegProfilePanel({ className }: RegProfilePanelProps): JSX.Element {
  const ready = useModelReady();
  const plot = usePlotDoc();
  const display = useUnitsDisplay();
  const actions = usePlotActions();

  const packList = useRulepackList();
  const storedPack = plot.regProfile.cityPack;
  const uiPack = cityPackFromStored(storedPack);
  const pack = useRulepack(uiPack === 'custom' ? null : uiPack);

  if (!ready) {
    return (
      <PanelSection title="City rules" className={className ?? ''}>
        <SkeletonText lines={4} />
      </PanelSection>
    );
  }

  const boundaryAreaMm2 = plot.boundary.length >= 3 ? polygonAreaMm2(plot.boundary) : null;
  const facts = buildRegFacts({ boundaryAreaMm2, roads: plot.roads });
  const overrides = plot.regProfile.overrides;

  const resolved: ResolvedRegProfile =
    uiPack === 'custom'
      ? resolveRegValues(EMPTY_PACK, facts, overrides)
      : pack.state === 'ready'
        ? resolveRegValues(pack.data, facts, overrides)
        : { values: {}, missing: [] };

  const packSummary =
    packList.state === 'ready' ? (packList.data.find((p) => p.id === uiPack) ?? null) : null;

  const setOverride = (key: RegValueKey, value: number | null): void => {
    const next = withValueOverride(overrides, key, value);
    actions.setRegProfile(
      storedPack,
      next,
      value === null
        ? `${REG_VALUE_META[key].label} reset to the pack value`
        : `${REG_VALUE_META[key].label} override`,
    );
  };

  const commitChip = (key: RegValueKey, raw: string): void => {
    if (REG_VALUE_META[key].kind === 'length') {
      const parsed = tryParseLengthMm(raw, display);
      if (parsed.ok && parsed.mm > 0) setOverride(key, parsed.mm);
      return; // an unreadable edit leaves the pack value standing — no guessing
    }
    const scalar = parseRegScalar(key, raw);
    if (scalar !== null) setOverride(key, scalar);
  };

  const frontRoad = (() => {
    const idx = frontEdgeIndex(plot.roads);
    if (idx === null) return null;
    return plot.roads.find((r) => r.edgeIndex === idx) ?? null;
  })();

  return (
    <PanelSection title="City rules" className={className ?? ''}>
      <SelectField
        label="Rule preset"
        value={uiPack}
        onValueChange={(v) => {
          // Overrides are the architect's own numbers; switching city keeps them.
          actions.setRegProfile(cityPackToStored(v), overrides, 'City rules preset');
        }}
        options={CITY_PACK_OPTIONS.map((o) => ({ value: o.id, label: o.label }))}
        hint={
          packSummary !== null && packSummary.version !== ''
            ? `Pack ${packSummary.version}`
            : undefined
        }
      />

      {/* Seed honesty — a locked product decision, not decoration. */}
      {uiPack !== 'custom' && (packSummary === null || packSummary.confidence === 'seed') ? (
        <Chip severity="warn" size="sm" icon="alert-triangle" className="mt-2">
          Seed values — not yet reviewed by a local architect
        </Chip>
      ) : null}

      {/* What the numbers were resolved AGAINST (golden rule 4). */}
      <div className="mt-3 flex flex-wrap gap-1.5">
        <AssumptionChip
          label="Plot"
          valueText={
            boundaryAreaMm2 === null ? 'not drawn yet' : formatPlotArea(boundaryAreaMm2, display)
          }
          reason="Measured from the boundary you drew; the bye-law tables band on plot area."
          accepted={boundaryAreaMm2 !== null}
        />
        <AssumptionChip
          label="Front road"
          valueText={frontRoad?.widthMm != null ? formatLength(frontRoad.widthMm, 'm') : 'not set'}
          reason="The widest road on any edge counts as the front; FAR and height tables band on its width."
          accepted={frontRoad !== null}
        />
        <AssumptionChip
          label="Use"
          valueText="Residential dwelling"
          reason="Assumed single-family residential — the MVP's scope. The brief refines this later."
        />
      </div>

      {/* The resolved values */}
      <div className="mt-3">
        {uiPack !== 'custom' && pack.state === 'loading' ? <SkeletonText lines={4} /> : null}

        {uiPack !== 'custom' && pack.state === 'error' ? (
          <div className="rounded-md border border-fail-line bg-fail-soft px-3 py-2 text-xs text-fail-ink">
            <p>
              We couldn&apos;t load the {uiPack.toUpperCase()} rule pack. {pack.error.message}
            </p>
            <Button variant="secondary" size="sm" className="mt-2" onClick={pack.retry}>
              Try again
            </Button>
          </div>
        ) : null}

        {uiPack === 'custom' || pack.state === 'ready' ? (
          <ul className="space-y-1.5">
            {REG_VALUE_KEYS.map((key) => {
              const value = resolved.values[key];
              const missing = resolved.missing.find((m) => m.key === key) ?? null;
              const meta = REG_VALUE_META[key];

              if (value === undefined) {
                const reason =
                  missing?.reason ??
                  (uiPack === 'custom'
                    ? 'No city pack selected — click to enter the value from your local bye-law.'
                    : 'This pack has no rule for it yet.');
                return (
                  <li key={key} className="flex items-center gap-1.5">
                    <AssumptionChip
                      label={meta.label}
                      valueText="not set"
                      reason={reason}
                      onCommit={(raw) => commitChip(key, raw)}
                    />
                  </li>
                );
              }

              const cite =
                value.cite === null
                  ? (value.citationsBase ?? undefined)
                  : value.citationsBase === null
                    ? value.cite
                    : `${value.cite} — ${value.citationsBase}`;
              const reason = value.overridden
                ? 'Your override. The compliance report checks against this value and cites the pack value it replaced; the change is recorded in the audit trail.'
                : `${value.title === '' ? 'From the selected pack' : value.title}.${
                    value.confidence === 'seed'
                      ? ' Seed value — check against the current bye-law before submission.'
                      : ''
                  }`;

              return (
                <li key={key} className="flex items-center gap-1.5">
                  <AssumptionChip
                    label={meta.label}
                    valueText={formatRegValue(key, value.value, display)}
                    reason={reason}
                    {...(cite === undefined ? {} : { cite })}
                    onCommit={(raw) => commitChip(key, raw)}
                    accepted={value.overridden}
                  />
                  {value.overridden ? (
                    <button
                      type="button"
                      onClick={() => setOverride(key, null)}
                      className="garh-focus-ring rounded-sm text-2xs text-ink-subtle underline-offset-2 hover:underline"
                      title="Back to the pack value"
                    >
                      Reset
                    </button>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : null}
      </div>

      <p className="mt-3 text-2xs leading-4 text-ink-subtle">
        Overrides never silence a check. The compliance report checks against your number, keeps the
        pack&rsquo;s original value on the row for the citation trail, and records the change in the
        audit log.
      </p>
    </PanelSection>
  );
}
