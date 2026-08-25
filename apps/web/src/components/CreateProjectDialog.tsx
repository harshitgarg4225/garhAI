/**
 * CreateProjectDialog — the "New project" flow.
 *
 * F1 acceptance criterion: "plot from dims <60s". So the dialog asks for the
 * four things that unblock everything else — name, city, units, and a plot
 * rectangle — and nothing else. Everything further (north angle, roads per
 * edge, irregular boundary, DXF import) belongs on the plot editor, where there
 * is room to show what you are doing.
 *
 * The plot is optional and says so. An architect who has a name and a city but
 * not the survey numbers yet should still be able to make the project.
 *
 * The size fields are `LengthInput`s with `bareUnit` set to the project's
 * display units, so in a ft-in project typing "30" means 30 feet and typing
 * "9.144m" also works. Both land in the model as integer millimetres.
 */

import { useEffect, useMemo, useState } from 'react';
import { formatPlotArea, parseLengthMm } from '@garh/model';
import type { UnitsDisplay } from '@garh/model';
import {
  Button,
  Dialog,
  Field,
  Icon,
  Input,
  LengthInput,
  SelectField,
  cn,
} from '@garh/ui';

/** City packs shipped in the MVP (playbook §6 seeds blr / ncr / hyd). */
export const CITY_PACK_OPTIONS = [
  { value: 'blr', label: 'Bengaluru (BBMP)' },
  { value: 'ncr', label: 'Delhi NCR (MPD)' },
  { value: 'hyd', label: 'Hyderabad (GHMC / HMDA)' },
  { value: 'custom', label: 'Somewhere else — enter rules myself' },
] as const;

export type CityPackValue = (typeof CITY_PACK_OPTIONS)[number]['value'];

/** Sizes an Indian residential plot is usually quoted in. */
const QUICK_SIZES: ReadonlyArray<{ label: string; width: string; depth: string }> = [
  { label: "20 × 30 ft", width: "20'", depth: "30'" },
  { label: "30 × 40 ft", width: "30'", depth: "40'" },
  { label: "30 × 50 ft", width: "30'", depth: "50'" },
  { label: "40 × 60 ft", width: "40'", depth: "60'" },
  { label: "50 × 80 ft", width: "50'", depth: "80'" },
  { label: "60 × 90 ft", width: "60'", depth: "90'" },
];

export interface CreateProjectInput {
  name: string;
  clientName: string | undefined;
  cityPack: CityPackValue;
  units: UnitsDisplay;
  /** Omitted when the architect skipped the plot step. */
  plot: { widthMm: number; depthMm: number } | undefined;
}

export interface CreateProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreate: (input: CreateProjectInput) => void;
  creating?: boolean | undefined;
  /** Golden rule 8 — the demo is offered here too, not only on empty states. */
  onTryDemo?: (() => void) | undefined;
  /** Server-side failure, rendered inline rather than as a toast that vanishes. */
  error?: string | undefined;
}

export function CreateProjectDialog({
  open,
  onOpenChange,
  onCreate,
  creating = false,
  onTryDemo,
  error,
}: CreateProjectDialogProps): JSX.Element {
  const [name, setName] = useState('');
  const [clientName, setClientName] = useState('');
  const [cityPack, setCityPack] = useState<CityPackValue>('blr');
  const [units, setUnits] = useState<UnitsDisplay>('ft-in');
  const [widthMm, setWidthMm] = useState<number | null>(null);
  const [depthMm, setDepthMm] = useState<number | null>(null);
  const [touchedName, setTouchedName] = useState(false);

  // Reset when the dialog is re-opened, so a cancelled draft does not linger.
  useEffect(() => {
    if (!open) return;
    setName('');
    setClientName('');
    setCityPack('blr');
    setUnits('ft-in');
    setWidthMm(null);
    setDepthMm(null);
    setTouchedName(false);
  }, [open]);

  const areaMm2 = widthMm !== null && depthMm !== null ? widthMm * depthMm : null;
  const nameError = touchedName && name.trim() === '' ? 'Give the project a name so you can find it later.' : undefined;

  const halfPlot = useMemo(
    () => (widthMm === null) !== (depthMm === null),
    [widthMm, depthMm],
  );

  const submit = (): void => {
    setTouchedName(true);
    if (name.trim() === '') return;
    onCreate({
      name: name.trim(),
      clientName: clientName.trim() === '' ? undefined : clientName.trim(),
      cityPack,
      units,
      plot: widthMm !== null && depthMm !== null ? { widthMm, depthMm } : undefined,
    });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="New project"
      description="Two fields to start. You can change everything later."
      size="md"
      footer={
        <>
          {onTryDemo === undefined ? null : (
            <Button variant="ghost" iconLeft="play" onClick={onTryDemo} className="mr-auto">
              Try the demo project instead
            </Button>
          )}
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={creating}>
            Cancel
          </Button>
          <Button
            variant="primary"
            iconLeft="plus"
            loading={creating}
            loadingLabel="Creating your project"
            onClick={submit}
          >
            Create project
          </Button>
        </>
      }
    >
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <Field
          label="Project name"
          required
          error={nameError}
          hint="Most firms use the client's name and the locality."
        >
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              aria-describedby={describedBy}
              invalid={invalid}
              value={name}
              autoFocus
              placeholder="Sharma Residence, Whitefield"
              onChange={(e) => setName(e.target.value)}
              onBlur={() => setTouchedName(true)}
            />
          )}
        </Field>

        <Field label="Client name" hint="Optional. Shown on the drawing title block later.">
          {({ id, describedBy }) => (
            <Input
              id={id}
              aria-describedby={describedBy}
              value={clientName}
              placeholder="Mr & Mrs Sharma"
              onChange={(e) => setClientName(e.target.value)}
            />
          )}
        </Field>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <SelectField
            label="City rules"
            value={cityPack}
            onValueChange={(v) => setCityPack(v)}
            options={CITY_PACK_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
            hint="Sets setbacks, FAR and coverage. Every value stays editable."
          />
          <SelectField
            label="Show sizes in"
            value={units}
            onValueChange={(v) => setUnits(v)}
            options={[
              { value: 'ft-in', label: "Feet & inches (12'-6\")" },
              { value: 'm', label: 'Metres (3.81 m)' },
            ]}
            hint="Display only. Drawings are always dimensioned in mm."
          />
        </div>

        <fieldset className="rounded-lg border border-line p-3">
          <legend className="px-1 text-xs font-medium text-ink-muted">
            Plot size <span className="text-ink-subtle">— optional, you can draw it later</span>
          </legend>

          <div className="mb-3 flex flex-wrap gap-1.5">
            {QUICK_SIZES.map((size) => {
              const w = parseLengthMm(size.width, 'ft-in');
              const d = parseLengthMm(size.depth, 'ft-in');
              const active = widthMm === w && depthMm === d;
              return (
                <button
                  key={size.label}
                  type="button"
                  onClick={() => {
                    setWidthMm(w);
                    setDepthMm(d);
                  }}
                  className={cn(
                    'garh-focus-ring rounded-full border px-2.5 py-1 text-xs transition-colors',
                    active
                      ? 'border-brand/40 bg-brand-soft text-brand-ink'
                      : 'border-line text-ink-muted hover:bg-surface-muted hover:text-ink',
                  )}
                >
                  {size.label}
                </button>
              );
            })}
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <LengthInput
              label="Width (along the road)"
              valueMm={widthMm}
              onCommitMm={setWidthMm}
              display={units}
              bareUnit={units}
              minMm={1000}
              maxMm={200_000}
            />
            <LengthInput
              label="Depth"
              valueMm={depthMm}
              onCommitMm={setDepthMm}
              display={units}
              bareUnit={units}
              minMm={1000}
              maxMm={200_000}
            />
          </div>

          {areaMm2 !== null ? (
            <p className="mt-2 flex items-center gap-1.5 text-xs text-ink-muted garh-nums">
              <Icon name="check" size={13} className="text-pass" />
              {formatPlotArea(areaMm2, units)}
            </p>
          ) : halfPlot ? (
            <p className="mt-2 text-xs text-ink-muted">
              Add the other side and we will work out the area, or skip this and draw the plot
              later.
            </p>
          ) : null}
        </fieldset>

        {error === undefined ? null : (
          <p role="alert" className="flex items-start gap-1.5 rounded-md bg-fail-soft p-2.5 text-xs text-fail-ink">
            <Icon name="alert-circle" size={14} className="mt-px shrink-0" />
            <span>{error}</span>
          </p>
        )}

        {/* Enter should submit even though the real button lives in the footer. */}
        <button type="submit" className="sr-only" tabIndex={-1} aria-hidden="true">
          Create project
        </button>
      </form>
    </Dialog>
  );
}
