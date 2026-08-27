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
import { Button, Dialog, Field, Icon, Input, LengthInput, SelectField, cn } from '@garh/ui';
import { TemplatePicker } from './TemplatePicker';
import type { TemplateOption } from './TemplatePicker';

/** City packs shipped in the MVP (playbook §6 seeds blr / ncr / hyd). */
export const CITY_PACK_OPTIONS = [
  { value: 'blr', label: 'Bengaluru (BBMP)' },
  { value: 'ncr', label: 'Delhi NCR (MPD)' },
  { value: 'hyd', label: 'Hyderabad (GHMC / HMDA)' },
  { value: 'custom', label: 'Somewhere else — enter rules myself' },
] as const;

export type CityPackValue = (typeof CITY_PACK_OPTIONS)[number]['value'];

/** Sizes an Indian residential plot is usually quoted in. */
const QUICK_SIZES: readonly { label: string; width: string; depth: string }[] = [
  { label: '20 × 30 ft', width: "20'", depth: "30'" },
  { label: '30 × 40 ft', width: "30'", depth: "40'" },
  { label: '30 × 50 ft', width: "30'", depth: "50'" },
  { label: '40 × 60 ft', width: "40'", depth: "60'" },
  { label: '50 × 80 ft', width: "50'", depth: "80'" },
  { label: '60 × 90 ft', width: "60'", depth: "90'" },
];

/** The registry id of the empty template — mirrors `garh_api.templates.BLANK_TEMPLATE_ID`. */
export const BLANK_TEMPLATE_ID = 'blank';

export interface CreateProjectInput {
  name: string;
  clientName: string | undefined;
  cityPack: CityPackValue;
  units: UnitsDisplay;
  /** Omitted when the architect skipped the plot step. */
  plot: { widthMm: number; depthMm: number } | undefined;
  /**
   * The chosen starter template, applied server-side as an op-log recipe.
   * Undefined when the registry never loaded (the dialog degrades to blank).
   */
  templateId: string | undefined;
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
  /**
   * Starter templates from `GET /templates`, picker order ("Blank" first).
   * Undefined while loading or when the fetch failed — the dialog then behaves
   * exactly as before templates existed, which is the honest degraded state.
   */
  templates?: readonly TemplateOption[] | undefined;
}

export function CreateProjectDialog({
  open,
  onOpenChange,
  onCreate,
  creating = false,
  onTryDemo,
  error,
  templates,
}: CreateProjectDialogProps): JSX.Element {
  const [name, setName] = useState('');
  const [clientName, setClientName] = useState('');
  const [cityPack, setCityPack] = useState<CityPackValue>('blr');
  const [units, setUnits] = useState<UnitsDisplay>('ft-in');
  const [widthMm, setWidthMm] = useState<number | null>(null);
  const [depthMm, setDepthMm] = useState<number | null>(null);
  const [touchedName, setTouchedName] = useState(false);
  const [templateId, setTemplateId] = useState(BLANK_TEMPLATE_ID);

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
    setTemplateId(BLANK_TEMPLATE_ID);
  }, [open]);

  const selectedTemplate = (templates ?? []).find((t) => t.id === templateId);
  /** A non-blank template carries its own plot ops, so the manual plot step hides. */
  const templateHasPlot = selectedTemplate !== undefined && selectedTemplate.plotSizeLabel !== '';
  /**
   * A template whose recipe pins a city pack (its ops set the reg profile) forces
   * the city select — two sources for "which bye-laws apply" is the exact
   * liability CLAUDE.md's compliance rule exists to prevent.
   */
  const templateCity = selectedTemplate?.tags.find((tag): tag is CityPackValue =>
    CITY_PACK_OPTIONS.some((o) => o.value === tag),
  );

  const pickTemplate = (id: string): void => {
    setTemplateId(id);
    const picked = (templates ?? []).find((t) => t.id === id);
    const city = picked?.tags.find((tag): tag is CityPackValue =>
      CITY_PACK_OPTIONS.some((o) => o.value === tag),
    );
    if (city !== undefined) setCityPack(city);
  };

  const areaMm2 = widthMm !== null && depthMm !== null ? widthMm * depthMm : null;
  const nameError =
    touchedName && name.trim() === ''
      ? 'Give the project a name so you can find it later.'
      : undefined;

  const halfPlot = useMemo(() => (widthMm === null) !== (depthMm === null), [widthMm, depthMm]);

  const submit = (): void => {
    setTouchedName(true);
    if (name.trim() === '') return;
    onCreate({
      name: name.trim(),
      clientName: clientName.trim() === '' ? undefined : clientName.trim(),
      cityPack,
      units,
      plot:
        !templateHasPlot && widthMm !== null && depthMm !== null ? { widthMm, depthMm } : undefined,
      // Sent verbatim, "blank" included — the registry lists blank explicitly.
      // Undefined only when the registry never loaded, which the server treats
      // identically to blank.
      templateId: templates === undefined ? undefined : templateId,
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

        {templates !== undefined && templates.length > 0 ? (
          <div>
            <p className="mb-1.5 text-xs font-medium text-ink-muted">
              Start from{' '}
              <span className="font-normal text-ink-subtle">
                — a template seeds the plot and brief; everything stays editable.
              </span>
            </p>
            <TemplatePicker
              templates={templates}
              value={templateId}
              onChange={pickTemplate}
              disabled={creating}
            />
          </div>
        ) : null}

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <SelectField
            label="City rules"
            value={cityPack}
            onValueChange={(v) => setCityPack(v)}
            options={CITY_PACK_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
            disabled={templateCity !== undefined}
            hint={
              templateCity === undefined
                ? 'Sets setbacks, FAR and coverage. Every value stays editable.'
                : 'Set by the template. You can switch city packs in the plot panel.'
            }
          />
          <SelectField
            label="Show sizes in"
            value={units}
            onValueChange={(v) => setUnits(v)}
            options={[
              { value: 'ft-in', label: 'Feet & inches (12\'-6")' },
              { value: 'm', label: 'Metres (3.81 m)' },
            ]}
            hint="Display only. Drawings are always dimensioned in mm."
          />
        </div>

        {templateHasPlot ? (
          <p className="flex items-start gap-1.5 rounded-lg border border-line bg-surface-muted p-2.5 text-xs text-ink-muted">
            <Icon name="check" size={13} className="mt-px shrink-0 text-pass" />
            <span>
              The template sets up a {selectedTemplate?.plotSizeLabel} plot, road and brief for you
              — adjust any of it once the project opens.
            </span>
          </p>
        ) : (
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
        )}

        {error === undefined ? null : (
          <p
            role="alert"
            className="flex items-start gap-1.5 rounded-md bg-fail-soft p-2.5 text-xs text-fail-ink"
          >
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
