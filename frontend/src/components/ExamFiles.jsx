/**
 * ExamFiles.jsx — which files of a class stay open in exam mode.
 *
 * Data: GET /api/teacher/classes/{classId}/exam-files, and PUT the same with
 * { document_ids } (the ticked files, no others). Each tick is saved at once.
 * In exam mode students search and open only these files; with none, the
 * agent answers without the course. The list can be prepared before the
 * exam, while exam mode is off.
 */
import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';

import api from '../lib/api';
import { useT } from '../lib/i18n';

export default function ExamFiles({ classId, examMode }) {
  const t = useT();
  const [files, setFiles] = useState(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => {
    api.get(`/api/teacher/classes/${classId}/exam-files`).then(setFiles).catch(() => setFiles([]));
  }, [classId]);
  useEffect(() => { setFiles(null); load(); }, [load]);

  const save = async (ids) => {
    setSaving(true);
    try {
      setFiles(await api.put(`/api/teacher/classes/${classId}/exam-files`, { document_ids: ids }));
    } catch (err) {
      toast.error(err.message);
      load();
    } finally {
      setSaving(false);
    }
  };
  const allowedIds = (files || []).filter((f) => f.exam_allowed).map((f) => f.id);
  const toggle = (file) => save(file.exam_allowed
    ? allowedIds.filter((id) => id !== file.id) : [...allowedIds, file.id]);

  if (files === null) return <p className="text-[13px] text-cream-muted">{t('t.exam.loading')}</p>;
  const groups = [];
  for (const f of files) {
    const name = f.lab_name || '';
    let group = groups.find((g) => g.name === name);
    if (!group) groups.push(group = { name, files: [] });
    group.files.push(f);
  }
  const n = allowedIds.length;
  return (
    <div className="mt-4 border-t border-border-subtle pt-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-[14px] font-medium text-cream">{t('t.exam.filesTitle')}</p>
        <span className={`text-[13px] ${n ? 'text-gold' : 'text-cream-muted'}`}>
          {n ? t('t.exam.nOpen', { n }) : t('t.exam.noneOpen')}
        </span>
      </div>
      <p className="mt-1 text-[13px] text-cream-muted">
        {t(examMode ? 't.exam.filesHelpOn' : 't.exam.filesHelpOff')}
      </p>
      {!files.length ? (
        <p className="mt-3 text-[13px] text-cream-muted">{t('t.exam.noFiles')}</p>
      ) : (
        <div className="mt-3 space-y-3">
          {groups.map((g) => (
            <div key={g.name}>
              <p className="mb-1 px-2 text-[13px] font-medium text-cream-muted">{g.name || t('t.exam.wholeClass')}</p>
              <ul className="space-y-0.5">
                {g.files.map((f) => (
                  <li key={f.id}>
                    <label className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-[14px] hover:bg-ink-hover">
                      <input type="checkbox" checked={f.exam_allowed} disabled={saving} onChange={() => toggle(f)}
                        className="h-4 w-4 cursor-pointer accent-[rgb(var(--ink))]" />
                      <span className="min-w-0 flex-1 truncate text-cream">{f.filename}</span>
                      {!['indexed', 'partially_indexed'].includes(f.status) && <span className="shrink-0 text-[12px] text-cream-muted">{t('t.exam.notReady')}</span>}
                    </label>
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {n > 0 && (
            <button type="button" disabled={saving} onClick={() => save([])}
              className="cursor-pointer rounded-lg px-2 py-1 text-[13px] text-cream-secondary hover:bg-ink-hover hover:text-cream disabled:opacity-50">
              {t('t.exam.closeAll')}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
