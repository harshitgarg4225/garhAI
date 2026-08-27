/**
 * useProjectCollab — ties the collaboration stream's lifetime to the project
 * shell's, the way every per-project hook in this directory does.
 *
 * Subscribe on mount, tear down on unmount or when the project id changes; the
 * teardown also resets the presence store so project B never renders project
 * A's avatars for a frame. All the actual wiring lives in
 * `stores/collab.startProjectCollab` — this hook exists only so the shell says
 * `useProjectCollab(projectId)` and nothing else.
 */

import { useEffect } from 'react';

import { startProjectCollab } from '../stores/collab';

export function useProjectCollab(projectId: string): void {
  useEffect(() => {
    if (projectId === '') return undefined;
    return startProjectCollab(projectId);
  }, [projectId]);
}

export default useProjectCollab;
