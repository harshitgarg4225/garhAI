/**
 * VastuSelector — OFF / Advisory / Strict, plus the editable zone preferences.
 *
 * The three modes are explained in plain language (§15 tone: no jargon), and
 * the zone table renders the classical defaults from the Vastu rule pack with
 * every zone EDITABLE (§F2: "all editable") — an architect whose client wants
 * kitchen-NW gets to say so, and the solver/scorer reads the preference from
 * `brief.data.vastuPrefs`, not from the pack's hardcoded ideal.
 *
 * Mode travels on the op's dedicated `vastuMode` payload field (it lives on
 * `BriefDoc.vastuMode`, not inside `data`); the same op's patch records
 * `vastuDecided: true` so the completeness meter can tell "chose Off" apart
 * from "never thought about it".
 */

import type { Direction8, JsonValue, VastuMode } from '@garh/model';
import { Button, Card, CardHeader, Icon, cn } from '@garh/ui';

import { DirectionPicker, ToggleField } from './fields';
import { VASTU_DEFAULT_PREFS, VASTU_ZONE_RULES, type VastuPrefs } from './types';
import { useBrief } from './useBrief';

export interface VastuSelectorProps {
  readonly className?: string | undefined;
}

const MODES: ReadonlyArray<{
  mode: VastuMode;
  title: string;
  blurb: string;
}> = [
  {
    mode: 'off',
    title: 'Off',
    blurb: 'Vastu is not considered. Plans are generated and scored without it.',
  },
  {
    mode: 'advisory',
    title: 'Advisory',
    blurb:
      'Every plan gets a Vastu score out of 100 with a per-rule breakdown. Nothing is blocked — the score informs your choice.',
  },
  {
    mode: 'strict',
    title: 'Strict',
    blurb:
      'The zone rules below become hard constraints in the layout solver. A plan that breaks one is never shown.',
  },
];

function prefsEqualDefaults(prefs: VastuPrefs): boolean {
  const same = (a: readonly Direction8[], b: readonly Direction8[]): boolean =>
    a.length === b.length && a.every((d, i) => d === b[i]);
  return (
    VASTU_ZONE_RULES.every((rule) => same(prefs[rule.key], VASTU_DEFAULT_PREFS[rule.key])) &&
    prefs.brahmasthanOpen === VASTU_DEFAULT_PREFS.brahmasthanOpen
  );
}

export function VastuSelector({ className }: VastuSelectorProps): JSX.Element {
  const { data, vastuMode, update } = useBrief();
  const prefs = data.vastuPrefs ?? VASTU_DEFAULT_PREFS;
  const isDefault = prefsEqualDefaults(prefs);

  const setMode = (mode: VastuMode): void => {
    update({
      patch: { vastuDecided: true },
      vastuMode: mode,
      label: `Vastu set to ${mode}`,
    });
  };

  // The whole prefs object is written each time: RFC 7386 would merge the
  // sub-object, but zone arrays REPLACE, and sending the complete set keeps
  // the stored shape self-contained for the solver.
  const commitPrefs = (next: VastuPrefs, label: string): void => {
    update({ patch: { vastuPrefs: next as unknown as JsonValue, vastuDecided: true }, label });
  };

  return (
    <Card className={className}>
      <CardHeader
        title="Vastu"
        description="How much the direction rules should shape the plans."
        actions={
          data.vastuDecided === true ? (
            <Icon name="check-circle" size={15} className="text-pass-ink" title="Decided" />
          ) : undefined
        }
      />

      {/* Mode — three radio cards */}
      <div className="grid gap-2 px-4 pb-4 sm:grid-cols-3" role="radiogroup" aria-label="Vastu mode">
        {MODES.map(({ mode, title, blurb }) => {
          const active = data.vastuDecided === true && vastuMode === mode;
          return (
            <button
              key={mode}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => setMode(mode)}
              className={cn(
                'garh-focus-ring rounded-lg border p-3 text-left transition-colors',
                active
                  ? 'border-brand bg-brand-soft'
                  : 'border-line bg-surface hover:border-ink-subtle',
              )}
            >
              <span className="flex items-center justify-between gap-2">
                <span className="text-sm font-semibold text-ink">{title}</span>
                {active ? <Icon name="check-circle" size={15} className="text-brand-ink" /> : null}
              </span>
              <span className="mt-1 block text-xs leading-5 text-ink-muted">{blurb}</span>
            </button>
          );
        })}
      </div>

      {/* Zone preferences — hidden while off, because they would do nothing */}
      {data.vastuDecided === true && vastuMode !== 'off' ? (
        <div className="border-t border-line">
          <div className="flex items-center justify-between gap-3 px-4 pt-3">
            <p className="text-xs text-ink-muted">
              Zone preferences. These are the classical defaults — change any of them and the{' '}
              {vastuMode === 'strict' ? 'solver constraints' : 'score'} follow your version.
            </p>
            {isDefault ? null : (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => commitPrefs(VASTU_DEFAULT_PREFS, 'Vastu zones reset to defaults')}
              >
                Reset to defaults
              </Button>
            )}
          </div>
          <ul className="divide-y divide-line px-4 py-1">
            {VASTU_ZONE_RULES.map((rule) => (
              <li key={rule.key} className="flex flex-wrap items-center gap-x-4 gap-y-1.5 py-2.5">
                <span className="min-w-36">
                  <span className="block text-sm text-ink">{rule.label}</span>
                  <span className="block text-2xs leading-4 text-ink-subtle">{rule.hint}</span>
                </span>
                <DirectionPicker
                  label={`${rule.label} zones`}
                  value={prefs[rule.key]}
                  onChange={(zones) =>
                    commitPrefs(
                      { ...prefs, [rule.key]: zones },
                      `Vastu ${rule.label.toLowerCase()} updated`,
                    )
                  }
                  className="ml-auto"
                />
              </li>
            ))}
            <li className="py-1">
              <ToggleField
                label="Keep the brahmasthan open"
                hint="The centre cell of the 3×3 grid stays free of enclosing walls."
                value={prefs.brahmasthanOpen}
                onChange={(v) =>
                  commitPrefs({ ...prefs, brahmasthanOpen: v }, 'Brahmasthan preference updated')
                }
              />
            </li>
          </ul>
        </div>
      ) : null}
    </Card>
  );
}

export default VastuSelector;
