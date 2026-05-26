/**
 * GlobalSidebar.jsx — Strategy B role-based navigation sidebar.
 *
 * Renders navigation links based on the authenticated user's role:
 *   admin   → Admin Panel, Teacher Console, AI Chat
 *   teacher → Teacher Console, AI Chat
 *   student → AI Chat only
 */

import { NavLink } from 'react-router-dom';
import useAuthStore from '../store/authStore';

const NAV_ITEMS = [
  {
    to: '/admin/users',
    label: 'Admin Panel',
    icon: ShieldIcon,
    roles: ['admin'],
  },
  {
    to: '/teacher/students',
    label: 'Teacher Console',
    icon: BookIcon,
    roles: ['admin', 'teacher'],
  },
  {
    to: '/chat',
    label: 'AI Chat',
    icon: ChatIcon,
    roles: ['admin', 'teacher', 'student'],
  },
];

export default function GlobalSidebar() {
  const userRole = useAuthStore((s) => s.userRole);

  const visibleItems = NAV_ITEMS.filter((item) =>
    item.roles.includes(userRole)
  );

  return (
    <aside
      className="fixed left-0 top-0 bottom-0 flex flex-col bg-bg-secondary border-r border-border-subtle z-40"
      style={{ width: 'var(--sidebar-width)' }}
    >
      {/* ── Brand ── */}
      <div className="flex items-center gap-3 px-6 h-[var(--header-height)] border-b border-border-subtle shrink-0">
        <div className="w-9 h-9 rounded-xl gradient-accent flex items-center justify-center shadow-[var(--shadow-glow)]">
          <SparkleIcon className="w-5 h-5 text-white" />
        </div>
        <div>
          <h1 className="text-base font-bold font-[var(--font-display)] tracking-tight text-text-primary leading-none">
            Edu-LLM
          </h1>
          <span className="text-[10px] tracking-widest uppercase text-text-muted font-medium">
            v3 Lean MVP
          </span>
        </div>
      </div>

      {/* ── Navigation ── */}
      <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
        <span className="block px-3 mb-2 text-[11px] font-semibold uppercase tracking-wider text-text-muted">
          Navigation
        </span>
        {visibleItems.map((item, i) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              `group flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-200 animate-slide-in-left ${
                isActive
                  ? 'bg-accent-muted text-accent shadow-sm'
                  : 'text-text-secondary hover:bg-bg-surface-hover hover:text-text-primary'
              }`
            }
            style={{ animationDelay: `${i * 60}ms` }}
          >
            <item.icon className="w-5 h-5 shrink-0" />
            {item.label}
          </NavLink>
        ))}
      </nav>

      {/* ── Footer ── */}
      <div className="px-4 py-3 border-t border-border-subtle">
        <div className="flex items-center gap-2 px-2 py-1.5 rounded-md text-text-muted text-xs">
          <div className="w-1.5 h-1.5 rounded-full bg-success animate-[pulse-glow_2s_ease-in-out_infinite]" />
          System Online
        </div>
      </div>
    </aside>
  );
}

/* ── Inline SVG Icons ── */

function ShieldIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    </svg>
  );
}

function BookIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20" />
    </svg>
  );
}

function ChatIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="m3 21 1.9-5.7a8.5 8.5 0 1 1 3.8 3.8z" />
    </svg>
  );
}

function SparkleIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 2L14.09 8.26L20 9.27L15.55 13.97L16.91 20L12 16.9L7.09 20L8.45 13.97L4 9.27L9.91 8.26L12 2Z" />
    </svg>
  );
}
