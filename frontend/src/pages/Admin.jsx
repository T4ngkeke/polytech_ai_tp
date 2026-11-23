/**
 * Admin.jsx — Admin dashboard.
 * Left nav rail: Users | Classes | Documents | Monitoring | LLM Config | Maintenance.
 * Each tab lives in admin/.
 */
import { useCallback, useState } from 'react';
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
import { ConfirmModal } from './admin/shared';
import { MenuItem } from './student/ui';
import { useT } from '../lib/i18n';

const NAV = [
  { key: 'users', label: 'a.nav.users', icon: 'users' },
  { key: 'classes', label: 'a.nav.classes', icon: 'grid' },
  { key: 'documents', label: 'a.nav.documents', icon: 'file' },
  { key: 'monitoring', label: 'a.nav.monitoring', icon: 'pulse' },
  { key: 'llm', label: 'a.nav.llm', icon: 'chip' },
  { key: 'maintenance', label: 'a.nav.maintenance', icon: 'database' },
];

export default function Admin() {
  const t = useT();
  const [active, setActive] = useState('users');
  // Phones: the navigation is a drawer opened from the top bar.
  const [navOpen, setNavOpen] = useState(false);
  // LLM Config reports unsaved changes; leaving the tab then asks first.
  const [llmDirty, setLlmDirty] = useState(false);
  const [leaveTo, setLeaveTo] = useState(null);
  const onLlmDirty = useCallback((dirty) => setLlmDirty(dirty), []);
  const go = (key) => {
    setNavOpen(false);
    if (key === active) return;
    if (active === 'llm' && llmDirty) setLeaveTo(key);
    else setActive(key);
  };
  const { username, logout } = useAuthStore();
  const navigate = useNavigate();

  const handleSignOut = () => {
    logout();
    navigate('/login', { replace: true });
  };

  return (
    <div className="flex flex-col md:flex-row h-dvh bg-ink-deep overflow-hidden">
      <div className="md:hidden shrink-0 h-12 px-2 flex items-center gap-2 border-b border-border-subtle bg-ink-base">
        <button type="button" onClick={() => setNavOpen(true)} title={t('a.menu')} aria-label={t('a.menu')}
          className="p-2 rounded-lg text-cream-secondary hover:text-cream hover:bg-ink-hover cursor-pointer">
          <Icon name="menu" className="w-5 h-5" />
        </button>
        <span className="text-[14px] font-medium text-cream">{t(NAV.find((n) => n.key === active)?.label)}</span>
      </div>
      {navOpen && (
        <div className="fixed inset-0 z-30 bg-black/40 md:hidden" onClick={() => setNavOpen(false)} />
      )}
      {/* Nav rail (a drawer on phones) */}
      <aside className={`${navOpen ? 'flex' : 'hidden'} fixed inset-y-0 left-0 z-40 w-64 shadow-elevated bg-ink-base
        md:static md:z-auto md:flex md:w-60 md:shadow-none shrink-0 flex-col border-r border-border-subtle`}>
        <div className="flex h-12 shrink-0 items-center gap-2 px-3">
          <span className="flex h-6 w-6 items-center justify-center rounded-md bg-ink text-[13px] font-semibold text-on-ink">E</span>
          <span className="text-[15px] font-semibold text-cream">Edu-LLM</span>
          <span className="text-[12.5px] text-cream-muted">{t('a.caption')}</span>
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto px-2 pt-2" aria-label={t('a.caption')}>
          {NAV.map(({ key, label, icon }) => (
            <MenuItem key={key} icon={icon} label={t(label)} active={active === key} onClick={() => go(key)} />
          ))}
        </nav>
        <div className="shrink-0 border-t border-border-subtle px-3 pb-2 pt-3">
          <div className="flex items-center gap-2.5 px-2 py-1">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-ink text-[13px] font-medium text-on-ink">
              {(username || 'A').charAt(0).toUpperCase()}
            </span>
            <span className="min-w-0 flex-1 truncate text-[14px] text-cream">{username || 'Admin'}</span>
            <ThemeToggle className="!p-1.5 !rounded-lg" />
            <button type="button" onClick={() => navigate('/account')} title={t('t.sb.account')} aria-label={t('t.sb.account')}
              className="cursor-pointer rounded-lg p-1.5 text-cream-secondary hover:bg-ink-hover hover:text-cream">
              <Icon name="settings" className="h-4 w-4" />
            </button>
            <button type="button" onClick={handleSignOut} title={t('logout')} aria-label={t('logout')}
              className="cursor-pointer rounded-lg p-1.5 text-cream-secondary hover:bg-ink-hover hover:text-cream">
              <Icon name="logout" className="h-4 w-4" />
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
        {active === 'llm' && <LLMConfigTab onDirtyChange={onLlmDirty} />}
        {active === 'maintenance' && <MaintenanceTab />}
      </main>
      {leaveTo && (
        <ConfirmModal title={t('a.leave.title')} danger confirmLabel={t('a.leave.confirm')}
          message={t('a.leave.message')}
          onCancel={() => setLeaveTo(null)}
          onConfirm={() => { setActive(leaveTo); setLeaveTo(null); setLlmDirty(false); }} />
      )}
    </div>
  );
}
