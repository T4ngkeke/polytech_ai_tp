/**
 * teacher/SettingsPanels.jsx — "Settings" tabs of a lab and of a class.
 */
import { useState } from 'react';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import ExamFiles from '../../components/ExamFiles';
import Icon from '../../components/Icon';
import { useT } from '../../lib/i18n';
import { Switch } from '../student/ui';
import { ClassFeatures, LabFeatures } from './Features';

export function LabSettingsPanel({ lab, onToggle, examMode, onLabChange }) {
  const t = useT();
  return (
    <div className="max-w-2xl space-y-4">
      <h3 className="text-[15px] font-semibold text-cream">{t('t.lab.settings')}</h3>
      <div className="flex items-center justify-between gap-3 rounded-2xl border border-border-subtle bg-ink-raised p-5">
        <div className="min-w-0">
          <p className="text-[14px] font-medium text-cream">{t('t.lab.status')}</p>
          <p className="text-[13px] text-cream-muted">{lab.is_active ? t('t.lab.open') : t('t.lab.locked')}</p>
        </div>
        <button onClick={onToggle}
          className={`shrink-0 rounded-lg px-3.5 py-1.5 text-[13px] cursor-pointer hover:bg-ink-hover ${lab.is_active ? 'text-danger' : 'text-cream'}`}>
          <span className="inline-flex items-center gap-1.5">
            <Icon name={lab.is_active ? 'lock' : 'unlock'} className="w-3.5 h-3.5" />
            {lab.is_active ? t('t.lock') : t('t.unlock')}
          </span>
        </button>
      </div>
      <LabFeatures lab={lab} examMode={examMode} onLabChange={onLabChange} />
    </div>
  );
}

export function ClassSettingsPanel({ cls, onChange, onLabChange }) {
  const t = useT();
  const [saving, setSaving] = useState(false);
  const toggle = async () => {
    setSaving(true);
    try {
      onChange(await api.put(`/api/teacher/classes/${cls.id}/exam-mode`, { exam_mode: !cls.exam_mode }));
      toast.success(t(cls.exam_mode ? 't.exam.toastOff' : 't.exam.toastOn'));
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="max-w-3xl space-y-4">
      <section className="rounded-2xl border border-border-subtle bg-ink-raised p-5">
        <h3 className="mb-4 text-[15px] font-semibold text-cream">{t('t.class.settings')}</h3>
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="text-[14px] font-medium text-cream">{t('t.exam.title')}</p>
            <p className="mt-1 text-[13px] text-cream-muted">
              {t(cls.exam_mode ? 't.exam.on' : 't.exam.off')}
            </p>
          </div>
          <Switch checked={!!cls.exam_mode} onChange={toggle} disabled={saving} label={t('t.exam.title')} />
        </div>
        <ExamFiles classId={cls.id} examMode={!!cls.exam_mode} />
      </section>
      <ClassFeatures cls={cls} labs={cls.labs || []} onClassChange={onChange} onLabChange={onLabChange} />
    </div>
  );
}
