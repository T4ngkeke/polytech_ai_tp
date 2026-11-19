/**
 * Admin.jsx — Admin dashboard.
 * Left nav rail: Users | Classes | Documents | Monitoring | LLM Config | Maintenance.
 * Each tab lives in admin/.
 */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import useAuthStore from '../store/authStore';
import ThemeToggle from '../components/ThemeToggle';
import Icon from '../components/Icon';
import MonitoringPanel from '../components/MonitoringPanel';
import UsersTab from './admin/UsersTab';
import ClassesTab from './admin/ClassesTab';
import DocumentsTab from './admin/DocumentsTab';
import LLMConfigTab from './admin/LlmConfigTab';
import MaintenanceTab from './admin/MaintenanceTab';

const NAV = [
  { key: 'users', label: 'Users', icon: 'users' },
  { key: 'classes', label: 'Classes', icon: 'grid' },
  { key: 'documents', label: 'Documents', icon: 'file' },
  { key: 'monitoring', label: 'Monitoring', icon: 'pulse' },
  { key: 'llm', label: 'LLM Config', icon: 'chip' },
  { key: 'maintenance', label: 'Maintenance', icon: 'database' },
];

export default function Admin() {
  const [active, setActive] = useState('users');
  // Phones: the navigation is a drawer opened from the top bar.
  const [navOpen, setNavOpen] = useState(false);
  const { username, logout } = useAuthStore();
  const navigate = useNavigate();

  const handleSignOut = () => {
    logout();
    navigate('/login', { replace: true });
  };

  return (
    <div className="flex flex-col md:flex-row h-dvh bg-ink-deep overflow-hidden">
      <div className="md:hidden shrink-0 h-12 px-2 flex items-center gap-2 border-b border-border-subtle bg-ink-base">
        <button type="button" onClick={() => setNavOpen(true)} title="Menu" aria-label="Menu"
          className="p-2 rounded-lg text-cream-secondary hover:text-cream hover:bg-ink-hover cursor-pointer">
          <Icon name="menu" className="w-5 h-5" />
        </button>
        <span className="text-sm font-medium text-cream">{NAV.find((n) => n.key === active)?.label}</span>
      </div>
      {navOpen && (
        <div className="fixed inset-0 z-30 bg-black/40 md:hidden" onClick={() => setNavOpen(false)} />
      )}
      {/* Nav rail (a drawer on phones) */}
      <aside className={`${navOpen ? 'flex' : 'hidden'} fixed inset-y-0 left-0 z-40 w-64 shadow-elevated bg-ink-base
        md:static md:z-auto md:flex md:w-56 md:shadow-none md:bg-ink-base/50 shrink-0 flex-col border-r border-border-subtle`}>
        <div className="px-5 pt-6 pb-5">
          <h1 className="font-display text-lg text-cream leading-tight">Admin Console</h1>
          <p className="text-[11px] text-cream-muted mt-0.5">System administration</p>
        </div>
        <nav className="flex-1 px-3 space-y-1">
          {NAV.map(({ key, label, icon }) => {
            const on = active === key;
            return (
              <button key={key} onClick={() => { setActive(key); setNavOpen(false); }}
                className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors cursor-pointer ${
                  on ? 'bg-cyan-muted text-cyan' : 'text-cream-secondary hover:text-cream hover:bg-ink-hover'
                }`}>
                <Icon name={icon} className="w-[18px] h-[18px] shrink-0" />
                {label}
              </button>
            );
          })}
        </nav>
        <div className="p-3 border-t border-border-subtle">
          <div className="flex items-center gap-2 px-2 py-1.5">
            <div className="w-7 h-7 rounded-full bg-cyan-muted text-cyan grid place-items-center text-xs font-semibold shrink-0">
              {(username || 'A').charAt(0).toUpperCase()}
            </div>
            <span className="flex-1 min-w-0 text-sm font-medium text-cream truncate">{username || 'Admin'}</span>
            <ThemeToggle className="!p-1.5 !bg-transparent !border-transparent" />
            <button onClick={handleSignOut} title="Sign out"
              className="p-1.5 rounded-lg text-cream-muted hover:text-danger hover:bg-danger-muted transition-colors cursor-pointer">
              <Icon name="logout" className="w-4 h-4" />
            </button>
          </div>
        </div>
      </aside>

      {/* Content */}
      <main className="flex-1 min-h-0 overflow-hidden">
        {active === 'users' && <UsersTab />}
        {active === 'classes' && <ClassesTab />}
        {active === 'documents' && <DocumentsTab />}
        {active === 'monitoring' && <MonitoringPanel />}
        {active === 'llm' && <LLMConfigTab />}
        {active === 'maintenance' && <MaintenanceTab />}
      </main>
    </div>
  );
}
