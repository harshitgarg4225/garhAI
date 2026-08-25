/**
 * NotFoundPage — the 404.
 *
 * Golden rule 9 applies to routing too: say what happened, offer the next
 * action. Golden rule 8 applies as well — this is an empty state, so it offers
 * the demo project like every other one.
 */

import { useNavigate } from 'react-router-dom';
import { EmptyState, demoProjectAction, useToast } from '@garh/ui';
import { AppShell, PageBody, toProblem } from '../components';
import { useProjectStore } from '../stores/project';

export function NotFoundPage(): JSX.Element {
  const navigate = useNavigate();
  const { toast } = useToast();
  const ensureDemoProject = useProjectStore((s) => s.ensureDemoProject);

  const openDemo = async (): Promise<void> => {
    try {
      const demo = await ensureDemoProject();
      navigate(`/projects/${demo.id}/brief`);
    } catch (err) {
      toast({
        severity: 'fail',
        title: "Couldn't open the demo project",
        description: toProblem(err).message,
        action: { label: 'Back to your projects', onClick: () => navigate('/') },
      });
    }
  };

  return (
    <AppShell>
      <PageBody>
        <EmptyState
          icon="search"
          title="That page isn't here"
          description="The link may be old, or the project may have been renamed or archived. Your projects are all safe."
          action={{ label: 'Back to your projects', onClick: () => navigate('/'), icon: 'home' }}
          demoAction={demoProjectAction(() => void openDemo())}
        />
      </PageBody>
    </AppShell>
  );
}

export default NotFoundPage;
