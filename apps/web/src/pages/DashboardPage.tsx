/**
 * DashboardPage — the project list (F10).
 *
 * Status chips Brief/Options/Design/Drawings on every card, a create flow that
 * takes under a minute, and — golden rule 8 — an empty state that teaches the
 * next action and offers the seeded demo project.
 *
 * Loading shows project-card skeletons in the real grid, never a spinner (§15).
 * Errors render as a `ProblemPanel` with a working retry button, never a raw
 * exception (golden rule 9).
 *
 * STORE CONTRACT (see `./_contracts`):
 *   ../stores/session  -> useSessionStore : SessionSlice
 *   ../stores/project  -> useProjectStore : ProjectSlice
 *   ../stores/ui       -> useUiStore      : UiSlice
 */

import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import {
  Button,
  EmptyState,
  Icon,
  Input,
  SelectField,
  demoProjectAction,
  useToast,
} from '@garh/ui';
import {
  AppShell,
  CreateProjectDialog,
  PageBody,
  PageHeader,
  ProblemPanel,
  ProjectCard,
  ProjectCardSkeleton,
  toProblem,
} from '../components';
import type { CreateProjectInput, ProjectStage } from '../components';
import { TrialUsageCard, useUsage } from '../features/billing';
import { api } from '../lib/api';
import type { ProjectTemplate } from '../lib/api';
import { useProjectStore } from '../stores/project';
import { useSessionStore } from '../stores/session';
import { toProjectSummary } from './_contracts';

type SortKey = 'recent' | 'name';
type FilterKey = 'active' | 'all' | 'archived';

/** Where each dashboard chip jumps to inside a project. */
const STAGE_ROUTE: Readonly<Record<ProjectStage, string>> = {
  brief: 'brief',
  options: 'plan',
  design: 'plan',
  drawings: 'sheets',
};

export function DashboardPage(): JSX.Element {
  const navigate = useNavigate();
  const { toast } = useToast();

  const user = useSessionStore((s) => s.user);
  const firm = useSessionStore((s) => s.firm);
  const signOut = useSessionStore((s) => s.signOut);

  const items = useProjectStore((s) => s.items);
  const loading = useProjectStore((s) => s.loading);
  const error = useProjectStore((s) => s.error);
  const load = useProjectStore((s) => s.load);
  const create = useProjectStore((s) => s.create);
  const ensureDemoProject = useProjectStore((s) => s.ensureDemoProject);

  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<SortKey>('recent');
  const [filter, setFilter] = useState<FilterKey>('active');
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | undefined>(undefined);
  const [openingDemo, setOpeningDemo] = useState(false);
  /** Undefined until GET /templates answers; the dialog degrades to blank-only. */
  const [templates, setTemplates] = useState<ProjectTemplate[] | undefined>(undefined);
  // `/?new=1&template=<id>` opens the dialog on that template — the Plan tab's
  // "start from a ready-made plan" link lands here.
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTemplate = searchParams.get('template');
  useEffect(() => {
    if (searchParams.get('new') === null) return;
    setCreateOpen(true);
    setSearchParams({}, { replace: true });
  }, [searchParams, setSearchParams]);
  const { usage, loading: usageLoading, error: usageError } = useUsage();

  useEffect(() => {
    void load();
  }, [load]);

  // Fetch the starter templates the first time the create dialog opens. A
  // failure is deliberately silent: the dialog then works exactly as it did
  // before templates existed, which beats blocking "New project" on a registry.
  useEffect(() => {
    if (!createOpen || templates !== undefined) return;
    api.templates.list().then(setTemplates, () => undefined);
  }, [createOpen, templates]);

  const summaries = useMemo(() => (items ?? []).map(toProjectSummary), [items]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = summaries.filter((p) => {
      if (filter === 'active' && p.status === 'archived') return false;
      if (filter === 'archived' && p.status !== 'archived') return false;
      if (q === '') return true;
      return (
        p.name.toLowerCase().includes(q) ||
        (p.clientName ?? '').toLowerCase().includes(q) ||
        (p.cityLabel ?? '').toLowerCase().includes(q)
      );
    });
    return filtered.sort((a, b) =>
      sort === 'name'
        ? a.name.localeCompare(b.name)
        : Date.parse(b.updatedAt) - Date.parse(a.updatedAt),
    );
  }, [summaries, query, filter, sort]);

  const openDemo = async (): Promise<void> => {
    setOpeningDemo(true);
    try {
      const demo = await ensureDemoProject();
      navigate(`/projects/${demo.id}/brief`);
    } catch (err) {
      const problem = toProblem(err);
      toast({
        severity: 'fail',
        title: "Couldn't open the demo project",
        description: problem.message,
        action: { label: 'Try again', onClick: () => void openDemo() },
      });
    } finally {
      setOpeningDemo(false);
    }
  };

  const handleCreate = async (input: CreateProjectInput): Promise<void> => {
    setCreating(true);
    setCreateError(undefined);
    const fromTemplate = input.templateId !== undefined && input.templateId !== 'blank';
    try {
      const project = await create({
        name: input.name,
        clientName: input.clientName,
        cityPack: input.cityPack,
        units: input.units,
        plot: input.plot,
        templateId: input.templateId,
      });
      setCreateOpen(false);
      toast({
        severity: 'pass',
        title: `${project.name} is ready`,
        description: fromTemplate
          ? 'The template set up the plot and brief — review them, then generate options.'
          : 'Next: fill in the brief so we can generate plan options.',
      });
      navigate(`/projects/${project.id}/brief`);
    } catch (err) {
      setCreateError(toProblem(err).message);
    } finally {
      setCreating(false);
    }
  };

  const firstLoad = items === undefined && loading;
  const isEmpty = items !== undefined && summaries.length === 0;
  const noMatches = items !== undefined && summaries.length > 0 && visible.length === 0;

  return (
    <AppShell
      firmName={firm?.name}
      userName={user?.name}
      onSignOut={() => void signOut()}
      renderHomeLink={({ className, children }) => (
        <Link to="/" className={className}>
          {children}
        </Link>
      )}
      headerActions={
        <Button variant="primary" size="sm" iconLeft="plus" onClick={() => setCreateOpen(true)}>
          New project
        </Button>
      }
    >
      <PageBody>
        <PageHeader
          title="Projects"
          description={
            firm?.name === undefined
              ? 'Everything your studio is working on.'
              : `Everything ${firm.name} is working on.`
          }
          actions={
            items !== undefined && summaries.length > 0 ? (
              <>
                <Input
                  iconLeft="search"
                  placeholder="Search projects"
                  aria-label="Search projects"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  className="w-52"
                />
                <SelectField
                  label="Show"
                  labelHidden
                  value={filter}
                  onValueChange={(v) => setFilter(v)}
                  options={[
                    { value: 'active', label: 'Active' },
                    { value: 'all', label: 'All' },
                    { value: 'archived', label: 'Archived' },
                  ]}
                  fieldClassName="w-32"
                />
                <SelectField
                  label="Sort"
                  labelHidden
                  value={sort}
                  onValueChange={(v) => setSort(v)}
                  options={[
                    { value: 'recent', label: 'Recently updated' },
                    { value: 'name', label: 'Name (A–Z)' },
                  ]}
                  fieldClassName="w-44"
                />
              </>
            ) : undefined
          }
        />

        <TrialUsageCard usage={usage} loading={usageLoading} error={usageError} />

        {error !== null ? (
          <ProblemPanel
            problem={error}
            onRetry={() => void load()}
            onNavigate={(to) => navigate(to)}
          />
        ) : firstLoad ? (
          <div
            role="status"
            aria-live="polite"
            aria-busy="true"
            className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
          >
            <span className="sr-only">Loading your projects</span>
            <ProjectCardSkeleton />
            <ProjectCardSkeleton />
            <ProjectCardSkeleton />
          </div>
        ) : isEmpty ? (
          <EmptyState
            icon="folder"
            title="No projects yet"
            description={
              <>
                Start with a plot and a brief — we will generate compliant plan options in about a
                minute. Or open the demo project to see the whole flow first.
              </>
            }
            action={{ label: 'New project', onClick: () => setCreateOpen(true), icon: 'plus' }}
            demoAction={demoProjectAction(() => void openDemo(), openingDemo)}
          >
            <ul className="mt-4 flex max-w-md flex-col gap-1.5 text-left text-xs text-ink-muted">
              <li className="flex gap-2">
                <Icon name="check" size={13} className="mt-0.5 text-pass" />A 30 × 40 ft plot takes
                about thirty seconds to enter.
              </li>
              <li className="flex gap-2">
                <Icon name="check" size={13} className="mt-0.5 text-pass" />
                Bengaluru, Delhi NCR and Hyderabad bye-laws are built in.
              </li>
              <li className="flex gap-2">
                <Icon name="check" size={13} className="mt-0.5 text-pass" />
                Nothing is final — every change is undoable.
              </li>
            </ul>
          </EmptyState>
        ) : noMatches ? (
          <EmptyState
            size="sm"
            icon="search"
            title="Nothing matches that"
            description={`No project matches “${query.trim()}”. Try a shorter search, or change the filter.`}
            action={{ label: 'Clear search', onClick: () => setQuery('') }}
            demoAction={demoProjectAction(() => void openDemo(), openingDemo)}
          />
        ) : (
          <>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {visible.map((project) => (
                <ProjectCard
                  key={project.id}
                  project={project}
                  href={`/projects/${project.id}/brief`}
                  renderLink={({ href, className, children }) => (
                    <Link to={href} className={className}>
                      {children}
                    </Link>
                  )}
                  onStageClick={(stage) =>
                    navigate(`/projects/${project.id}/${STAGE_ROUTE[stage]}`)
                  }
                />
              ))}
            </div>

            {/* The demo offer stays reachable even when the list is not empty:
                §15's first-run flow assumes it is always one click away. */}
            {summaries.some((p) => p.isDemo) ? null : (
              <div className="mt-6 flex items-center justify-between gap-3 rounded-lg border border-dashed border-line-strong bg-surface p-4">
                <div>
                  <h2 className="text-sm font-semibold text-ink">New to Garh AI?</h2>
                  <p className="mt-0.5 text-xs text-ink-muted">
                    The demo project is a solved 30 × 40 ft Bengaluru plot — plan, 3D, renders and a
                    drawing set, all editable.
                  </p>
                </div>
                <Button
                  variant="secondary"
                  iconLeft="play"
                  loading={openingDemo}
                  onClick={() => void openDemo()}
                >
                  Open the demo project
                </Button>
              </div>
            )}
          </>
        )}
      </PageBody>

      <CreateProjectDialog
        templates={templates}
        initialTemplateId={requestedTemplate ?? undefined}
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreate={(input) => void handleCreate(input)}
        creating={creating}
        error={createError}
        onTryDemo={() => {
          setCreateOpen(false);
          void openDemo();
        }}
      />
    </AppShell>
  );
}

export default DashboardPage;
