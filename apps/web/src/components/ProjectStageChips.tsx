/**
 * ProjectStageChips — the F10 dashboard status chips.
 *
 * Product spec F10: "Dashboard (status chips: Brief/Options/Design/Drawings)".
 * Four chips, always all four, always in that order. Showing only the completed
 * ones would hide the path; showing them greyed shows the architect exactly
 * where a project stopped, which is the whole reason the chips exist.
 *
 * `stale` is a real state, not a decoration: renders and sheets carry a
 * `stale` flag when the model changes after they were generated (§9, §7), and
 * the dashboard is where you notice that before sending a client a drawing set
 * that no longer matches the plan.
 */

import { Chip, Tooltip } from '@garh/ui';
import type { ChipSeverity } from '@garh/ui';
import { PROJECT_STAGES } from './types';
import type { ProjectStage, ProjectStages, StageState } from './types';

const STAGE_LABEL: Readonly<Record<ProjectStage, string>> = {
  brief: 'Brief',
  options: 'Options',
  design: 'Design',
  drawings: 'Drawings',
};

/** What each chip means, in the architect's words. Shown on hover. */
const STAGE_HELP: Readonly<Record<ProjectStage, Readonly<Record<StageState, string>>>> = {
  brief: {
    todo: 'No brief yet. Start here — plot, rooms and Vastu preference.',
    active: 'Brief started. Fill the rest to get better plan options.',
    done: 'Brief complete.',
    stale: 'The plot changed after the brief was written. Worth a re-read.',
  },
  options: {
    todo: 'No plan options generated yet.',
    active: 'Options generated — none chosen yet.',
    done: 'An option is applied to the design.',
    stale: 'The brief changed since these options were generated.',
  },
  design: {
    todo: 'Nothing drawn yet.',
    active: 'Design in progress.',
    done: 'Design settled.',
    stale: 'Compliance has not been re-checked since the last edit.',
  },
  drawings: {
    todo: 'No drawing set generated yet.',
    active: 'Drawing set part-generated.',
    done: 'Drawing set ready to export.',
    stale: 'The design changed after these sheets were generated. Regenerate before submitting.',
  },
};

const STATE_SEVERITY: Readonly<Record<StageState, ChipSeverity>> = {
  todo: 'neutral',
  active: 'info',
  done: 'pass',
  stale: 'warn',
};

export interface ProjectStageChipsProps {
  stages: ProjectStages;
  size?: 'sm' | 'md' | undefined;
  /** Jump straight to that section of the project. */
  onStageClick?: ((stage: ProjectStage) => void) | undefined;
  className?: string | undefined;
}

export function ProjectStageChips({
  stages,
  size = 'sm',
  onStageClick,
  className,
}: ProjectStageChipsProps): JSX.Element {
  return (
    <ul
      className={
        className === undefined ? 'flex flex-wrap gap-1.5' : `flex flex-wrap gap-1.5 ${className}`
      }
    >
      {PROJECT_STAGES.map((stage) => {
        const state = stages[stage];
        return (
          <li key={stage}>
            <Tooltip content={STAGE_HELP[stage][state]} delayMs={200}>
              <Chip
                severity={STATE_SEVERITY[state]}
                size={size}
                icon={state === 'todo' ? null : undefined}
                onClick={onStageClick === undefined ? undefined : () => onStageClick(stage)}
              >
                {STAGE_LABEL[stage]}
              </Chip>
            </Tooltip>
          </li>
        );
      })}
    </ul>
  );
}

/** Convenience for pages: a project with nothing done yet. */
export const EMPTY_STAGES: ProjectStages = {
  brief: 'todo',
  options: 'todo',
  design: 'todo',
  drawings: 'todo',
};
