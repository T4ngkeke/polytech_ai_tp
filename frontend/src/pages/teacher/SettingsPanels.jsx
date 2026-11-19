/**
 * teacher/SettingsPanels.jsx — "Settings" tabs of a lab and of a class.
 */
import { useState } from 'react';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import ExamFiles from '../../components/ExamFiles';
import Icon from '../../components/Icon';

export function LabSettingsPanel({ lab, onToggle }) {
  return (
    <div className="max-w-md space-y-4">
      <h3 className="text-sm font-semibold text-cream mb-4">Lab Settings</h3>
      <div className="flex items-center justify-between px-4 py-4 rounded-xl bg-ink-raised border border-border-subtle">
        <div>
          <p className="text-sm font-medium text-cream">Lab Status</p>
          <p className="text-xs text-cream-muted">{lab.is_active ? 'Students can chat in this lab' : 'Lab is locked — no new messages'}</p>
        </div>
        <button onClick={onToggle}
          className={`px-4 py-2 rounded-lg text-xs font-semibold transition-all cursor-pointer ${
            lab.is_active ? 'bg-danger-muted text-danger hover:bg-danger/20' : 'bg-success-muted text-success hover:bg-success/25'
          }`}>
          <span className="inline-flex items-center gap-1.5">
            <Icon name={lab.is_active ? 'lock' : 'unlock'} className="w-3.5 h-3.5" />
            {lab.is_active ? 'Lock' : 'Unlock'}
          </span>
        </button>
      </div>
    </div>
  );
}

export function ClassSettingsPanel({ cls, onChange }) {
  const [saving, setSaving] = useState(false);
  const toggle = async () => {
    setSaving(true);
    try {
      onChange(await api.put(`/api/teacher/classes/${cls.id}/exam-mode`, { exam_mode: !cls.exam_mode }));
      toast.success(cls.exam_mode ? 'Exam mode off' : 'Exam mode on');
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="max-w-2xl p-5 rounded-xl bg-ink-raised border border-border-subtle">
      <h3 className="text-sm font-semibold text-cream mb-4">Class Settings</h3>
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-cream">Exam mode</p>
          <p className="mt-1 text-xs text-cream-muted">
            {cls.exam_mode
              ? 'On: in every lab of this class, students can search and open only the files ticked below; the others are closed, even pages cited in earlier answers. With no file ticked, students get Direct answers only.'
              : 'Off: students choose Direct, Search or Research. Turn it on during an exam: only the files ticked below stay open.'}
          </p>
        </div>
        <button type="button" role="switch" aria-checked={!!cls.exam_mode} aria-label="Exam mode"
          onClick={toggle} disabled={saving}
          className={`shrink-0 w-9 h-5 rounded-full transition-colors relative cursor-pointer disabled:opacity-50 ${cls.exam_mode ? 'bg-gold' : 'bg-ink-surface border border-border-default'}`}>
          <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-all ${cls.exam_mode ? 'left-[18px]' : 'left-0.5'}`} />
        </button>
      </div>
      <ExamFiles classId={cls.id} examMode={!!cls.exam_mode} />
    </div>
  );
}
