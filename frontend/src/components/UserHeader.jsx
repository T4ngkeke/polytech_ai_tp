/**
 * UserHeader.jsx — Top header bar with username display and logout button.
 */

import { useNavigate } from 'react-router-dom';
import useAuthStore from '../store/authStore';

const ROLE_COLORS = {
  admin: 'bg-danger-muted text-danger',
  teacher: 'bg-accent-warm-muted text-accent-warm',
  student: 'bg-accent-muted text-accent',
};

export default function UserHeader() {
  const navigate = useNavigate();
  const username = useAuthStore((s) => s.username);
  const userRole = useAuthStore((s) => s.userRole);
  const logout = useAuthStore((s) => s.logout);

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  const roleColor = ROLE_COLORS[userRole] || ROLE_COLORS.student;

  return (
    <header
      className="sticky top-0 z-30 flex items-center justify-between px-6 border-b border-border-subtle glass"
      style={{ height: 'var(--header-height)' }}
    >
      {/* Left — Page breadcrumb area (extensible) */}
      <div />

      {/* Right — User info + Logout */}
      <div className="flex items-center gap-4">
        {/* Role badge */}
        <span className={`px-2.5 py-1 rounded-full text-[11px] font-semibold uppercase tracking-wider ${roleColor}`}>
          {userRole}
        </span>

        {/* Username */}
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-full gradient-accent flex items-center justify-center text-white text-xs font-bold uppercase">
            {username ? username.charAt(0) : '?'}
          </div>
          <span className="text-sm font-medium text-text-primary hidden sm:inline">
            {username}
          </span>
        </div>

        {/* Logout */}
        <button
          id="logout-button"
          onClick={handleLogout}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-text-secondary hover:text-danger hover:bg-danger-muted transition-colors duration-200 cursor-pointer"
        >
          <LogoutIcon className="w-4 h-4" />
          <span className="hidden sm:inline">Logout</span>
        </button>
      </div>
    </header>
  );
}

function LogoutIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <polyline points="16 17 21 12 16 7" />
      <line x1="21" y1="12" x2="9" y2="12" />
    </svg>
  );
}
