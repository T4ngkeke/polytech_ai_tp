/**
 * MainLayout.jsx — Minimal layout shell for Edu-LLM v6.
 *
 * v6: Each page (Chat, Teacher, Admin) manages its own sidebar internally.
 * MainLayout only provides the authenticated route outlet — no global sidebar.
 */

import { Outlet } from 'react-router-dom';

export default function MainLayout() {
  return (
    <div className="flex flex-col h-screen bg-ink-deep overflow-hidden">
      <Outlet />
    </div>
  );
}
