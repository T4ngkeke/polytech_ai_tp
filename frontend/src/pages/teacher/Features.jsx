/**
 * teacher/Features.jsx — what students may use: upload, web search, Python,
 * deep research (backend services/feature_service.py). A class has one switch
 * per feature (PUT /api/teacher/classes/{id}/features); a lab follows the class
 * or has its own choice (PUT /api/teacher/labs/{id}/features: on / off /
 * inherit). Exam mode turns them all off.
 */
import { useState } from 'react';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import { useT } from '../../lib/i18n';
import { Switch } from '../student/ui';

export const FEATURES = ['upload', 'web', 'code', 'deep'];

function Choice({ value, onChange, label, disabled }) {
  const t = useT();
  const options = [['inherit', t('feat.inherit')], ['on', t('feat.on')], ['off', t('feat.off')]];
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label} disabled={disabled}
      className={`rounded-lg border border-border-default bg-ink-raised px-2 py-1 text-[12.5px] ${value === 'inherit' ? 'text-cream-muted' : 'text-cream'} disabled:opacity-50`}>
      {options.map(([v, text]) => <option key={v} value={v}>{text}</option>)}
    </select>
  );
}

export function ClassFeatures({ cls, labs = [], onClassChange, onLabChange }) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const features = cls.features || {};
  const setClass = async (key, on) => {
    setBusy(true);
    try { onClassChange(await api.put(`/api/teacher/classes/${cls.id}/features`, { [key]: on })); } catch (err) { toast.error(err.message); } finally { setBusy(false); }
  };
  const setLab = async (lab, key, value) => {
    try { onLabChange(await api.put(`/api/teacher/labs/${lab.id}/features`, { [key]: value })); } catch (err) { toast.error(err.message); }
  };
  return (
    <section className="rounded-2xl border border-border-subtle bg-ink-raised p-5">
      <h3 className="text-[15px] font-semibold text-cream">{t('feat.title')}</h3>
      <p className="mt-1 text-[13px] text-cream-muted">{cls.exam_mode ? t('feat.examNote') : t('feat.help')}</p>
      <div className="mt-3 divide-y divide-border-subtle">
        {FEATURES.map((key) => (
          <label key={key} htmlFor={`feat-${key}`} className="flex items-center gap-4 py-3">
            <span className="min-w-0 flex-1">
              <span className="block text-[14px] text-cream">{t(`feat.${key}`)}</span>
              <span className="block text-[12.5px] text-cream-muted">{t(`feat.${key}.help`)}</span>
            </span>
            <Switch id={`feat-${key}`} checked={features[key] !== false} disabled={busy} label={t(`feat.${key}`)}
              onChange={(on) => setClass(key, on)} />
          </label>
        ))}
      </div>
      {labs.length > 0 && (
        <div className="mt-4 overflow-x-auto">
          <div className="mb-2 text-[13px] font-medium text-cream-secondary">{t('feat.perLab')}</div>
          <table className="w-full min-w-[520px] text-left text-[13px]">
            <thead>
              <tr className="text-cream-muted">
                <th className="py-1.5 pr-3 font-normal">{t('feat.lab')}</th>
                {FEATURES.map((key) => <th key={key} className="px-2 py-1.5 font-normal">{t(`feat.${key}`)}</th>)}
              </tr>
            </thead>
            <tbody>
              {labs.map((lab) => (
                <tr key={lab.id} className="border-t border-border-subtle">
                  <td className="max-w-[180px] truncate py-2 pr-3 text-cream">{lab.name}</td>
                  {FEATURES.map((key) => (
                    <td key={key} className="px-2 py-2">
                      <Choice value={lab.feature_overrides?.[key] || 'inherit'} label={`${lab.name} · ${t(`feat.${key}`)}`}
                        onChange={(v) => setLab(lab, key, v)} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function LabFeatures({ lab, examMode, onLabChange }) {
  const t = useT();
  const setLab = async (key, value) => {
    try { onLabChange(await api.put(`/api/teacher/labs/${lab.id}/features`, { [key]: value })); } catch (err) { toast.error(err.message); }
  };
  return (
    <section className="rounded-2xl border border-border-subtle bg-ink-raised p-5">
      <h3 className="text-[15px] font-semibold text-cream">{t('feat.labTitle')}</h3>
      <p className="mt-1 text-[13px] text-cream-muted">{examMode ? t('feat.examNote') : t('feat.labHelp')}</p>
      <div className="mt-3 divide-y divide-border-subtle">
        {FEATURES.map((key) => (
          <div key={key} className="flex items-center gap-4 py-3">
            <span className="min-w-0 flex-1">
              <span className="block text-[14px] text-cream">{t(`feat.${key}`)}</span>
              <span className="block text-[12.5px] text-cream-muted">
                {t(lab.features?.[key] ? 'feat.nowOn' : 'feat.nowOff')}
              </span>
            </span>
            <Choice value={lab.feature_overrides?.[key] || 'inherit'} label={t(`feat.${key}`)} onChange={(v) => setLab(key, v)} />
          </div>
        ))}
      </div>
    </section>
  );
}
