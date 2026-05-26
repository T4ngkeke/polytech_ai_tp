/**
 * MainLayout.jsx — Global layout shell with sidebar + header + content outlet.
 */

import { Outlet } from 'react-router-dom';
import GlobalSidebar from './GlobalSidebar';
import UserHeader from './UserHeader';

export default function MainLayout() {
  return (
    <div className="min-h-screen bg-bg-primary">
      <GlobalSidebar />

      {/* Main content area — offset by sidebar width */}
      <div
        className="min-h-screen flex flex-col"
        style={{ marginLeft: 'var(--sidebar-width)' }}
      >
        <UserHeader />
        <main className="flex-1 overflow-hidden">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
