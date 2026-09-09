/**
 * PracticeSection — the firm's identity, and the title block the sheets print.
 *
 * Two cards, two endpoints, on purpose:
 *
 *  - PRACTICE (`GET/PATCH /firm`): name, address, GSTIN, registration, phone.
 *    Firm identity, stored under `firms.settings.practice`. The GSTIN is checked
 *    by the server with the real check digit; a typo comes back as a 422 naming
 *    the field, and the form shows that message, not "invalid".
 *
 *  - TITLE BLOCK (`GET/PUT /firm/drawing-preferences`): exactly the fields the
 *    drawings worker prints (`TitleBlockFields`). Only the firm-level ones are
 *    editable here — project name, client and date are per project and belong
 *    to the sheets editor. The PUT re-sends every other preference unchanged,
 *    because that route replaces the whole template.
 *
 * A rename in the first card is what the second card prints when no template
 * `firmName` has been saved, and it is what the app shell shows — `setFirmName`
 * keeps the header honest without a reload.
 */

import { useEffect, useState } from 'react';
import { Button, Card, CardBody, CardHeader, Field, Input, Textarea, useToast } from '@garh/ui';

import { ProblemPanel, toProblem } from '../../components';
import type { Problem } from '../../components';
import { api } from '../../lib/api';
import type { FirmProfile } from '../../lib/api';
import { AppError, ERROR_CODES } from '../../lib/errors';
import type { DrawingPreferencesResponse } from '../../lib/schemas';
import { selectIsAdmin, useSessionStore } from '../../stores/session';

interface PracticeForm {
  name: string;
  address: string;
  gstin: string;
  registrationNumber: string;
  phone: string;
}

interface TitleBlockForm {
  firmName: string;
  drawnBy: string;
  checkedBy: string;
  notes: string;
  logoUrl: string;
}

function practiceForm(firm: FirmProfile): PracticeForm {
  return {
    name: firm.name,
    address: firm.practice.address,
    gstin: firm.practice.gstin,
    registrationNumber: firm.practice.registrationNumber,
    phone: firm.practice.phone,
  };
}

function titleBlockForm(prefs: DrawingPreferencesResponse): TitleBlockForm {
  return {
    firmName: prefs.titleBlock.firmName,
    drawnBy: prefs.titleBlock.drawnBy,
    checkedBy: prefs.titleBlock.checkedBy,
    notes: prefs.titleBlock.notes,
    logoUrl: prefs.titleBlock.logoUrl ?? '',
  };
}

/** A 422 names the field; pull its message out so the form can show it in place. */
function fieldMessage(error: AppError, field: string): string | undefined {
  const errors = error.data.errors;
  if (!Array.isArray(errors)) return undefined;
  for (const item of errors) {
    if (typeof item !== 'object' || item === null) continue;
    const record = item as { field?: unknown; message?: unknown };
    if (record.field === field && typeof record.message === 'string') return record.message;
  }
  return undefined;
}

export function PracticeSection(): JSX.Element {
  const isAdmin = useSessionStore(selectIsAdmin);
  const setFirmName = useSessionStore((s) => s.setFirmName);
  const { toast } = useToast();

  const [firm, setFirm] = useState<FirmProfile | null>(null);
  const [prefs, setPrefs] = useState<DrawingPreferencesResponse | null>(null);
  const [form, setForm] = useState<PracticeForm | null>(null);
  const [block, setBlock] = useState<TitleBlockForm | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [gstinError, setGstinError] = useState<string | undefined>(undefined);
  const [savingFirm, setSavingFirm] = useState(false);
  const [savingBlock, setSavingBlock] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setProblem(null);
    Promise.all([api.team.firm(), api.sheets.preferences()])
      .then(([profile, preferences]) => {
        if (cancelled) return;
        setFirm(profile);
        setForm(practiceForm(profile));
        setPrefs(preferences);
        setBlock(titleBlockForm(preferences));
      })
      .catch((err: unknown) => {
        if (!cancelled) setProblem(toProblem(err));
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const savePractice = async (): Promise<void> => {
    if (form === null || firm === null) return;
    setSavingFirm(true);
    setGstinError(undefined);
    setProblem(null);
    try {
      const updated = await api.team.updateFirm({
        ...(form.name.trim() !== firm.name ? { name: form.name.trim() } : {}),
        practice: {
          address: form.address,
          gstin: form.gstin.trim(),
          registrationNumber: form.registrationNumber.trim(),
          phone: form.phone.trim(),
        },
      });
      setFirm(updated);
      setForm(practiceForm(updated));
      setFirmName(updated.name);
      toast({ severity: 'pass', title: 'Practice saved' });
    } catch (err) {
      const error = AppError.from(err);
      const message = fieldMessage(error, 'practice.gstin') ?? fieldMessage(error, 'gstin');
      if (error.code === ERROR_CODES.validationFailed && message !== undefined) {
        setGstinError(message);
      } else {
        setProblem(toProblem(error));
      }
    } finally {
      setSavingFirm(false);
    }
  };

  const saveTitleBlock = async (): Promise<void> => {
    if (block === null || prefs === null) return;
    setSavingBlock(true);
    setProblem(null);
    try {
      const saved = await api.sheets.savePreferences({
        titleBlock: {
          ...prefs.titleBlock,
          firmName: block.firmName.trim(),
          drawnBy: block.drawnBy.trim(),
          checkedBy: block.checkedBy.trim(),
          notes: block.notes.trim(),
          logoUrl: block.logoUrl.trim() === '' ? null : block.logoUrl.trim(),
        },
        dimToJamb: prefs.dimToJamb,
        sheetNumberPrefix: prefs.sheetNumberPrefix,
        defaultScaleDenominator: prefs.defaultScaleDenominator,
        sheetLayout: prefs.sheetLayout,
        revisions: prefs.revisions,
      });
      setPrefs(saved);
      setBlock(titleBlockForm(saved));
      toast({
        severity: 'pass',
        title: 'Title block saved',
        description: 'The next drawing set prints it.',
      });
    } catch (err) {
      setProblem(toProblem(err));
    } finally {
      setSavingBlock(false);
    }
  };

  if (problem !== null && (form === null || block === null)) {
    return <ProblemPanel problem={problem} onRetry={() => setReloadKey((k) => k + 1)} />;
  }
  if (form === null || block === null) {
    return (
      <p className="text-sm text-ink-muted" aria-busy="true">
        Loading your practice…
      </p>
    );
  }

  const readOnly = !isAdmin;
  const update = <K extends keyof PracticeForm>(key: K, value: PracticeForm[K]): void =>
    setForm((current) => (current === null ? current : { ...current, [key]: value }));
  const updateBlock = <K extends keyof TitleBlockForm>(key: K, value: TitleBlockForm[K]): void =>
    setBlock((current) => (current === null ? current : { ...current, [key]: value }));

  return (
    <div className="flex flex-col gap-6">
      {problem !== null ? (
        <ProblemPanel problem={problem} onRetry={() => setProblem(null)} />
      ) : null}

      <Card>
        <CardHeader
          title="Practice"
          description={
            readOnly
              ? 'Only an admin can change these. Ask one if something is wrong.'
              : 'What goes on the letterhead, the invoice and every sheet.'
          }
        />
        <CardBody>
          <form
            className="flex flex-col gap-4"
            data-testid="practice-form"
            onSubmit={(e) => {
              e.preventDefault();
              void savePractice();
            }}
          >
            <Field label="Practice name" required hint="Printed in the title block of every sheet.">
              {({ id, describedBy, invalid }) => (
                <Input
                  id={id}
                  value={form.name}
                  aria-describedby={describedBy}
                  invalid={invalid}
                  disabled={readOnly}
                  onChange={(e) => update('name', e.target.value)}
                />
              )}
            </Field>
            <Field label="Address" hint="As it should appear on invoices and drawings.">
              {({ id, describedBy }) => (
                <Textarea
                  id={id}
                  rows={3}
                  value={form.address}
                  aria-describedby={describedBy}
                  disabled={readOnly}
                  onChange={(e) => update('address', e.target.value)}
                />
              )}
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field
                label="GSTIN"
                error={gstinError}
                hint="15 characters. Checked against the GST check digit, so a typo is caught here."
              >
                {({ id, describedBy, invalid }) => (
                  <Input
                    id={id}
                    placeholder="29AABCG1234H1ZV"
                    value={form.gstin}
                    aria-describedby={describedBy}
                    invalid={invalid}
                    disabled={readOnly}
                    onChange={(e) => {
                      update('gstin', e.target.value.toUpperCase());
                      if (gstinError !== undefined) setGstinError(undefined);
                    }}
                  />
                )}
              </Field>
              <Field
                label="CoA / licence number"
                hint="The practice's registration. Your own CoA number lives under Account."
              >
                {({ id, describedBy }) => (
                  <Input
                    id={id}
                    placeholder="CA/2011/52345"
                    value={form.registrationNumber}
                    aria-describedby={describedBy}
                    disabled={readOnly}
                    onChange={(e) => update('registrationNumber', e.target.value)}
                  />
                )}
              </Field>
            </div>
            <Field label="Phone" hint="The number a client or a municipal office should call.">
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  type="tel"
                  inputMode="tel"
                  placeholder="+91 98765 43210"
                  value={form.phone}
                  aria-describedby={describedBy}
                  disabled={readOnly}
                  onChange={(e) => update('phone', e.target.value)}
                />
              )}
            </Field>
            {readOnly ? null : (
              <div className="flex justify-end">
                <Button type="submit" variant="primary" loading={savingFirm} loadingLabel="Saving">
                  Save practice
                </Button>
              </div>
            )}
          </form>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Title block"
          description="Exactly what the drawings worker prints on every sheet. Project name, client and date are set per project on the Sheets tab."
        />
        <CardBody>
          <form
            className="flex flex-col gap-4"
            data-testid="title-block-form"
            onSubmit={(e) => {
              e.preventDefault();
              void saveTitleBlock();
            }}
          >
            <Field label="Firm name on sheets" hint="Leave blank to print the practice name above.">
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  value={block.firmName}
                  placeholder={form.name}
                  aria-describedby={describedBy}
                  disabled={readOnly}
                  onChange={(e) => updateBlock('firmName', e.target.value)}
                />
              )}
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Drawn by">
                {({ id }) => (
                  <Input
                    id={id}
                    value={block.drawnBy}
                    disabled={readOnly}
                    onChange={(e) => updateBlock('drawnBy', e.target.value)}
                  />
                )}
              </Field>
              <Field label="Checked by">
                {({ id }) => (
                  <Input
                    id={id}
                    value={block.checkedBy}
                    disabled={readOnly}
                    onChange={(e) => updateBlock('checkedBy', e.target.value)}
                  />
                )}
              </Field>
            </div>
            <Field
              label="Notes"
              hint="Up to 240 characters. Statutory lines, a disclaimer, a motto."
            >
              {({ id, describedBy }) => (
                <Textarea
                  id={id}
                  rows={2}
                  maxLength={240}
                  value={block.notes}
                  aria-describedby={describedBy}
                  disabled={readOnly}
                  onChange={(e) => updateBlock('notes', e.target.value)}
                />
              )}
            </Field>
            <Field
              label="Logo URL"
              hint="An https:// image. It is fetched when the sheet is drawn."
            >
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  type="url"
                  placeholder="https://…/logo.png"
                  value={block.logoUrl}
                  aria-describedby={describedBy}
                  disabled={readOnly}
                  onChange={(e) => updateBlock('logoUrl', e.target.value)}
                />
              )}
            </Field>
            {readOnly ? null : (
              <div className="flex justify-end">
                <Button type="submit" variant="primary" loading={savingBlock} loadingLabel="Saving">
                  Save title block
                </Button>
              </div>
            )}
          </form>
        </CardBody>
      </Card>
    </div>
  );
}
