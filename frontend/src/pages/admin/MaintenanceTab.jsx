/**
 * admin/MaintenanceTab.jsx — Admin "Maintenance" tab: prune old chat sessions.
 */
import { useState } from 'react';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import Icon from '../../components/Icon';
import { plural } from '../../lib/plural';
import { PageHeader, ConfirmModal } from './shared';

/* ═══════════════════════════ MAINTENANCE TAB ═════════════════════════════ */
export default function MaintenanceTab() {
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
      toast.success(`Pruned ${plural(res.sessions_deleted, 'session')}`);
      setConfirmOpen(false);
    } catch (err) { toast.error(err.message); } finally { setPruning(false); }
  };

  return (
    <div className="p-4 md:p-8 overflow-y-auto h-full">
      <div className="max-w-md mx-auto">
        <PageHeader title="Database Maintenance" subtitle="Permanently remove old sessions to free disk space." />
        <div className="p-5 rounded-xl bg-danger-muted border border-danger/20 space-y-4">
          <p className="flex items-center gap-2 text-sm font-semibold text-danger">
            <Icon name="warning" className="w-4 h-4" /> Danger Zone — Irreversible
          </p>
          <div className="flex items-center gap-3">
            <span className="text-sm text-cream-secondary">Delete sessions older than</span>
            <input type="number" min={1} value={days} onChange={(e) => setDays(Number(e.target.value))}
              className="w-16 text-center bg-ink-deep border border-border-default text-cream text-sm px-2 py-1 rounded-lg focus:outline-none focus:border-danger/40" />
            <span className="text-sm text-cream-secondary">days</span>
          </div>
          <button onClick={openConfirm} disabled={pruning || !(days >= 1)}
            className="w-full py-2.5 rounded-lg bg-danger text-on-accent text-sm font-semibold hover:bg-danger-hover transition-all cursor-pointer disabled:opacity-40">
            Run Prune
          </button>
        </div>
        {result && (
          <div className="mt-6 p-4 rounded-xl bg-ink-raised border border-border-subtle text-sm space-y-1">
            <p className="text-cream">Sessions deleted: <span className="text-danger font-mono">{result.sessions_deleted}</span></p>
            <p className="text-cream">Messages deleted: <span className="text-danger font-mono">{result.messages_deleted}</span></p>
          </div>
        )}
      </div>

      {confirmOpen && (
        <ConfirmModal title="Prune old sessions" danger busy={pruning || !preview}
          message={preview
            ? <>Permanently delete what is older than <span className="text-cream font-medium">{plural(days, 'day')}</span>:{' '}
                <span className="text-cream font-medium">{plural(preview.sessions_deleted, 'session')}</span> with{' '}
                <span className="text-cream font-medium">{plural(preview.messages_deleted, 'message')}</span>.
                This cannot be undone.</>
            : <>Counting what is older than {plural(days, 'day')}…</>}
          confirmLabel={pruning ? 'Pruning…' : 'Run Prune'}
          onConfirm={handlePrune} onCancel={() => setConfirmOpen(false)} />
      )}
    </div>
  );
}
