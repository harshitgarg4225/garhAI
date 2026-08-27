/**
 * TemplatePicker — the "Start from" card grid inside the new-project dialog.
 *
 * Purely presentational (folder contract): the dialog passes the registry rows
 * `GET /templates` returned and owns the selection state. Cards behave as a
 * radiogroup — one template is always selected, "Blank" being the first and the
 * default — because a create must be unambiguous about what it seeds.
 *
 * The plot-size chip is the one fact an architect scans for ("do you have my
 * plot?"), so it sits top-right on the card rather than inside the prose.
 */

import { Icon, cn } from '@garh/ui';

/** One registry card, as the dialog receives it (`api.templates.list()` shape). */
export interface TemplateOption {
  id: string;
  name: string;
  description: string;
  /** "30 × 40 ft"; empty for the blank template (no chip rendered). */
  plotSizeLabel: string;
  tags: string[];
}

export interface TemplatePickerProps {
  templates: readonly TemplateOption[];
  /** The selected template id. */
  value: string;
  onChange: (templateId: string) => void;
  disabled?: boolean | undefined;
}

export function TemplatePicker({
  templates,
  value,
  onChange,
  disabled = false,
}: TemplatePickerProps): JSX.Element {
  return (
    <div
      role="radiogroup"
      aria-label="Start from a template"
      className="grid grid-cols-1 gap-2 sm:grid-cols-2"
    >
      {templates.map((template) => {
        const selected = template.id === value;
        return (
          <button
            key={template.id}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={disabled}
            onClick={() => onChange(template.id)}
            className={cn(
              'garh-focus-ring rounded-lg border p-3 text-left transition-colors',
              selected
                ? 'border-brand/40 bg-brand-soft'
                : 'border-line bg-surface hover:bg-surface-muted',
              disabled && 'cursor-not-allowed opacity-60',
            )}
          >
            <span className="flex items-start justify-between gap-2">
              <span className={cn('text-sm font-medium', selected ? 'text-brand-ink' : 'text-ink')}>
                {template.name}
              </span>
              {template.plotSizeLabel === '' ? (
                selected ? (
                  <Icon name="check" size={14} className="mt-0.5 shrink-0 text-brand-ink" />
                ) : null
              ) : (
                <span
                  className={cn(
                    'garh-nums shrink-0 rounded-full border px-2 py-0.5 text-[11px]',
                    selected ? 'border-brand/40 text-brand-ink' : 'border-line text-ink-muted',
                  )}
                >
                  {template.plotSizeLabel}
                </span>
              )}
            </span>
            <span className="mt-1 block text-xs leading-snug text-ink-muted">
              {template.description}
            </span>
          </button>
        );
      })}
    </div>
  );
}
