/**
 * admin/MaintenanceTab.jsx — Admin "Maintenance" tab: prune old chat sessions.
 */
import { useState } from 'react';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import Icon from '../../components/Icon';
import { useT } from '../../lib/i18n';
import { PageHeader, ConfirmModal, useCount } from './shared';

/* ═══════════════════════════ MAINTENANCE TAB ═════════════════════════════ */
export default function MaintenanceTab() {
  const t = useT();
  const count = useCount();
  const [days, setDays] = useState(30);
  const [pruning, setPruning] = useState(false);
  const [result, setResult] = useState(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [preview, setPreview] = useState(null); // what the prune would delete

  // Count first (dry run), so the confirmation names what will be deleted.
  const openConfirm = async () => {
    setPreview(null);
    setConfirmOpen(true);
    try {
      setPreview(await api.post('/api/admin/maintenance/prune', { older_than_days: days, dry_run: true }));
    } catch (err) {
      toast.error(err.message);
      setConfirmOpen(false);
    }
  };

  const handlePrune = async () => {
    setPruning(true);
    try {
      const res = await api.post('/api/admin/maintenance/prune', { older_than_days: days });
      setResult(res);
      toast.success(t('a.maint.pruned', { n: count('session', res.sessions_deleted) }));
      setConfirmOpen(false);
    } catch (err) { toast.error(err.message); } finally { setPruning(false); }
  };

  return (
    <div className="p-4 md:p-8 overflow-y-auto h-full">
      <div className="max-w-md mx-auto">
        <PageHeader title={t('a.maint.title')} subtitle={t('a.maint.subtitle')} />
        <div className="rounded-2xl border border-danger/30 bg-ink-raised p-5 space-y-4">
          <p className="flex items-center gap-2 text-[15px] font-semibold text-danger">
            <Icon name="warning" className="w-4 h-4" /> {t('a.maint.danger')}
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <label htmlFor="prune-days" className="text-[14px] text-cream-secondary">{t('a.maint.olderThan')}</label>
            <input id="prune-days" type="number" min={1} value={days} onChange={(e) => setDays(Number(e.target.value))}
              className="w-20 rounded-xl border border-border-default bg-ink-raised px-2 py-2 text-center text-[14px] text-cream focus:border-cream-muted focus:outline-none" />
            <span className="text-[14px] text-cream-secondary">{t('a.maint.days')}</span>
          </div>
          <button type="button" onClick={openConfirm} disabled={pruning || !(days >= 1)}
            className="w-full cursor-pointer rounded-full bg-danger px-3.5 py-2 text-[13px] text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40">
            {t('a.maint.run')}
          </button>
        </div>
        {result && (
          <div className="mt-5 rounded-2xl border border-border-subtle bg-ink-raised p-5 text-[14px] space-y-1">
            <p className="text-cream">{t('a.maint.sessionsDeleted')} <span className="text-danger tabular-nums">{result.sessions_deleted}</span></p>
            <p className="text-cream">{t('a.maint.messagesDeleted')} <span className="text-danger tabular-nums">{result.messages_deleted}</span></p>
          </div>
        )}
      </div>

      {confirmOpen && (
        <ConfirmModal title={t('a.maint.confirmTitle')} danger busy={pruning || !preview}
          message={preview
            ? t('a.maint.confirmMessage', {
              days: count('day', days), sessions: count('session', preview.sessions_deleted),
              messages: count('message', preview.messages_deleted) })
            : t('a.maint.counting', { days: count('day', days) })}
          confirmLabel={pruning ? t('a.maint.pruning') : t('a.maint.run')}
          onConfirm={handlePrune} onCancel={() => setConfirmOpen(false)} />
      )}
    </div>
  );
}
