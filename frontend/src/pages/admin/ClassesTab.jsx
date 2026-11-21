/**
 * admin/ClassesTab.jsx — Admin "Classes" tab: usage, labs and students per class.
 * Labs, students, usage, locking and deleting use the teacher routes, which
 * an admin may call on any class.
 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import Icon from '../../components/Icon';
import TeacherAnalytics, { DailyBars } from '../../components/TeacherAnalytics';
import { lastDays, lastWeek, sortClasses } from '../../lib/analytics';
import { useT } from '../../lib/i18n';
import {
  PageHeader, SortableTh, IconButton, EmptyState, Spinner, StatCard, Modal, ConfirmModal, useCount,
  inputClass, primaryButton, secondaryButton, outlineButton, cardClass,
} from './shared';

/* ═══════════════════════════ CLASSES TAB ═════════════════════════════════ */
// Platform numbers and every class with its teacher; a class opens on its
// Usage (the teacher Analytics view, without refill or conversations), Labs
// and Students. Replaces the old Oversight and Analytics tabs.
const CLASS_SECTIONS = ['usage', 'labs', 'students'];

export default function ClassesTab() {
  const t = useT();
  const count = useCount();
  const lastActive = (iso) => {
    if (!iso) return t('p.an.never');
    const start = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const n = Math.round((start(new Date()) - start(new Date(iso))) / 86400000);
    if (n <= 0) return t('p.an.today');
    if (n === 1) return t('p.an.yesterday');
    return t('p.an.daysAgo', { n });
  };
  const [classes, setClasses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [platform, setPlatform] = useState(null);
  const [teachers, setTeachers] = useState([]);
  const [sort, setSort] = useState({ key: 'teacher', dir: 'asc' });
  const [selectedClassId, setSelectedClassId] = useState(null);
  const [section, setSection] = useState('usage');
  const [labs, setLabs] = useState([]);
  const [students, setStudents] = useState([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [usageLab, setUsageLab] = useState('');
  const [usage, setUsage] = useState(null);
  // modal targets
  const [deleteClass, setDeleteClass] = useState(null);
  const [deleteLab, setDeleteLab] = useState(null);
  const [transferClass, setTransferClass] = useState(null);
  const [transferTo, setTransferTo] = useState('');
  const [busy, setBusy] = useState(false);

  const selectedClass = classes.find((c) => c.id === selectedClassId) || null;
  const days = useMemo(() => lastDays(platform?.daily_breakdown || []), [platform]);
  const rows = useMemo(() => sortClasses(classes, sort.key, sort.dir), [classes, sort]);

  const load = useCallback(async () => {
    try { setClasses(await api.get('/api/admin/classes')); }
    catch { toast.error(t('a.classes.loadFail')); } finally { setLoading(false); }
  }, [t]);

  useEffect(() => {
    load();
    api.get('/api/admin/analytics').then(setPlatform).catch(() => {});
    // teacher list for the transfer dropdown and the teacher count
    api.get('/api/admin/users')
      .then((us) => setTeachers(us.filter((u) => u.role === 'teacher' || u.role === 'admin')))
      .catch(() => {});
  }, [load]);

  const loadUsage = useCallback(async (classId, labId) => {
    setUsage(null);
    try {
      const q = labId ? `?lab_id=${labId}` : '';
      setUsage(await api.get(`/api/teacher/analytics/classes/${classId}/details${q}`));
    } catch (err) { toast.error(err.message); }
  }, []);

  const openClass = (cls) => {
    setSelectedClassId(cls.id);
    setSection('usage');
    setUsageLab('');
    setLabs([]); setStudents([]); setDetailLoading(true);
    Promise.all([
      api.get(`/api/teacher/classes/${cls.id}/labs`).then(setLabs),
      api.get(`/api/teacher/classes/${cls.id}/students`).then(setStudents),
    ]).catch(() => toast.error(t('a.classes.loadOneFail'))).finally(() => setDetailLoading(false));
    loadUsage(cls.id, '');
  };

  const pickUsageLab = (labId) => {
    setUsageLab(labId);
    loadUsage(selectedClassId, labId);
  };

  const toggleSort = (key) => setSort((s) => (s.key === key
    ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' }
    : { key, dir: key === 'name' || key === 'teacher' ? 'asc' : 'desc' }));

  const doDeleteClass = async () => {
    setBusy(true);
    try {
      await api.delete(`/api/teacher/classes/${deleteClass.id}`);
      setClasses((p) => p.filter((c) => c.id !== deleteClass.id));
      if (selectedClassId === deleteClass.id) setSelectedClassId(null);
      toast.success(t('a.classes.deleted'));
      setDeleteClass(null);
    } catch (err) { toast.error(err.message); } finally { setBusy(false); }
  };

  const doTransferClass = async () => {
    if (!transferTo) return;
    setBusy(true);
    try {
      await api.put(`/api/admin/classes/${transferClass.id}/transfer`, { teacher_id: transferTo });
      await load();               // the new teacher's name and status
      toast.success(t('a.classes.transferred'));
      setTransferClass(null);
    } catch (err) { toast.error(err.message); } finally { setBusy(false); }
  };

  const doDeleteLab = async () => {
    setBusy(true);
    try {
      await api.delete(`/api/teacher/labs/${deleteLab.id}`);
      setLabs((p) => p.filter((l) => l.id !== deleteLab.id));
      setClasses((p) => p.map((c) => (c.id === selectedClassId ? { ...c, lab_count: c.lab_count - 1 } : c)));
      toast.success(t('a.classes.labDeleted'));
      setDeleteLab(null);
    } catch (err) { toast.error(err.message); } finally { setBusy(false); }
  };

  const doToggleLab = async (lab) => {
    try {
      const updated = await api.put(`/api/teacher/labs/${lab.id}`, { is_active: !lab.is_active });
      setLabs((p) => p.map((l) => (l.id === lab.id ? updated : l)));
      toast.success(updated.is_active ? t('a.classes.labUnlocked') : t('a.classes.labLocked'));
    } catch (err) { toast.error(err.message); }
  };

  const transferButton = (cls) => (
    <IconButton title={t('a.classes.transferTo')} onClick={() => { setTransferClass(cls); setTransferTo(''); }}>
      <Icon name="swap" className="w-4 h-4" />
    </IconButton>
  );

  return (
    <div className="p-4 md:p-8 overflow-y-auto h-full">
      <div className="max-w-5xl mx-auto">
        {!selectedClass ? (
          /* ── Level 0: platform numbers + class table ── */
          <>
            <PageHeader title={t('a.nav.classes')}
              subtitle={`${count('class', classes.length)} · ${count('teacher', teachers.length)}`} />
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
              <StatCard label={t('a.classes.active7')} value={platform?.active_users_7d} />
              <StatCard label={t('a.classes.questions7')} value={platform ? lastWeek(days) : null} />
              <StatCard label={t('a.classes.questionsAll')} value={platform?.total_requests?.toLocaleString()} />
              <StatCard label={t('a.classes.tokensAll')} value={platform?.total_tokens?.toLocaleString()} />
            </div>
            {platform && (
              <section className={`${cardClass} mb-8`}>
                <h4 className="text-[15px] font-semibold text-cream mb-3">{t('a.classes.perDay')}</h4>
                <DailyBars days={days} />
              </section>
            )}

            {loading ? <Spinner /> : classes.length === 0 ? (
              <EmptyState icon={<Icon name="grid" className="w-7 h-7" />} message={t('a.classes.none')} />
            ) : (
              <div className="rounded-2xl border border-border-subtle bg-ink-raised overflow-x-auto">
                <table className="w-full min-w-[680px] text-[14px]">
                  <thead>
                    <tr className="border-b border-border-subtle text-[12.5px] text-cream-muted">
                      <SortableTh label={t('a.classes.col.class')} active={sort.key === 'name'} dir={sort.dir} onClick={() => toggleSort('name')} />
                      <SortableTh label={t('a.classes.col.teacher')} active={sort.key === 'teacher'} dir={sort.dir} onClick={() => toggleSort('teacher')} />
                      <th className="text-left font-normal px-4 py-2.5">{t('a.classes.col.labs')}</th>
                      <SortableTh label={t('a.classes.col.students')} active={sort.key === 'students'} dir={sort.dir} onClick={() => toggleSort('students')} />
                      <SortableTh label={t('a.classes.col.q7')} active={sort.key === 'questions_7d'} dir={sort.dir} onClick={() => toggleSort('questions_7d')} />
                      <SortableTh label={t('a.classes.col.last')} active={sort.key === 'last_active'} dir={sort.dir} onClick={() => toggleSort('last_active')} />
                      <th className="px-4 py-2.5" />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((cls) => (
                      <tr key={cls.id} onClick={() => openClass(cls)}
                        className="group border-b border-border-subtle last:border-0 cursor-pointer hover:bg-ink-hover/40 transition-colors">
                        <td className="px-4 py-2.5 max-w-[240px]">
                          <span className="font-medium text-cream truncate">{cls.name}</span>
                          {cls.exam_mode && (
                            <span className="ml-2 rounded bg-warn-soft px-1.5 py-0.5 text-[11px] text-gold" title={t('a.classes.examHint')}>
                              {t('exam.short')}
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-2.5">
                          <TeacherName cls={cls} />
                        </td>
                        <td className="px-4 py-2.5 text-[13px] text-cream-secondary tabular-nums">{cls.lab_count}</td>
                        <td className="px-4 py-2.5 text-[13px] text-cream-secondary tabular-nums">{cls.student_count}</td>
                        <td className="px-4 py-2.5 text-[13px] text-cream-secondary tabular-nums">{cls.questions_7d || <span className="text-cream-muted">0</span>}</td>
                        <td className="px-4 py-2.5 text-[13px] text-cream-secondary">{lastActive(cls.last_active)}</td>
                        <td className="px-2 py-1">
                          <div className="flex items-center justify-end gap-1 md:opacity-0 md:group-hover:opacity-100 transition-opacity"
                            onClick={(e) => e.stopPropagation()}>
                            {transferButton(cls)}
                            <IconButton title={t('a.classes.forceDelete')} danger onClick={() => setDeleteClass(cls)}>
                              <Icon name="trash" className="w-4 h-4" />
                            </IconButton>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        ) : (
          /* ── Level 1: one class (Usage, Labs, Students) ── */
          <>
            <button type="button" onClick={() => setSelectedClassId(null)}
              className="inline-flex items-center gap-1 text-[13px] text-cream-muted hover:text-cream transition-colors cursor-pointer mb-4">
              <Icon name="chevronRight" className="w-3.5 h-3.5 rotate-180" /> {t('a.nav.classes')}
            </button>
            <PageHeader title={selectedClass.name}
              subtitle={<>
                {t('a.classes.teacherLabel')} <TeacherName cls={selectedClass} /> · {count('lab', selectedClass.lab_count)}
                {' · '}{count('student', selectedClass.student_count)}
                {' · '}{t(selectedClass.exam_mode ? 'a.classes.examOn' : 'a.classes.examOff')}
              </>}>
              <button type="button" onClick={() => { setTransferClass(selectedClass); setTransferTo(''); }}
                className={`${outlineButton} cursor-pointer`}>
                <Icon name="swap" className="w-4 h-4" /> {t('a.classes.transfer')}
              </button>
            </PageHeader>
            {!selectedClass.teacher_ok && (
              <div className="mb-6 flex items-center gap-2 rounded-xl bg-danger-muted px-3 py-2 text-[13px] text-danger">
                <Icon name="warning" className="w-4 h-4 shrink-0" /> {t('a.classes.noTeacher')}
              </div>
            )}

            <div className="flex flex-wrap items-center gap-1 p-0.5 mb-6 w-fit max-w-full rounded-xl bg-ink-raised border border-border-subtle">
              {CLASS_SECTIONS.map((key) => (
                <button key={key} type="button" onClick={() => setSection(key)}
                  className={`px-3 py-1 rounded-lg text-[13px] transition-colors cursor-pointer ${
                    section === key ? 'bg-ink-hover text-cream' : 'text-cream-secondary hover:text-cream'
                  }`}>
                  {t(`a.classes.sec.${key}`)}
                </button>
              ))}
            </div>

            {section === 'usage' && (
              <>
                {labs.length > 1 && (
                  <select value={usageLab} onChange={(e) => pickUsageLab(e.target.value)} aria-label={t('a.classes.allLabs')}
                    className="mb-4 cursor-pointer rounded-xl border border-border-default bg-ink-raised px-3 py-1.5 text-[13px] text-cream focus:border-cream-muted focus:outline-none">
                    <option value="">{t('a.classes.allLabs')}</option>
                    {labs.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                  </select>
                )}
                <TeacherAnalytics data={usage} classId={selectedClass.id} refill={false} />
              </>
            )}

            {section === 'labs' && (detailLoading ? <p className="text-cream-muted text-[14px]">{t('a.loading')}</p> : labs.length ? (
              <div className="rounded-2xl border border-border-subtle bg-ink-raised overflow-hidden divide-y divide-border-subtle">
                {labs.map((lab) => {
                  const locked = lab.is_deleted || !lab.is_active;
                  return (
                    <div key={lab.id} className="group flex items-center gap-3 px-4 py-2.5">
                      <span className={`shrink-0 w-1.5 h-1.5 rounded-full ${locked ? 'bg-cream-muted' : 'bg-success'}`} />
                      <span className="flex-1 min-w-0 text-[14px] text-cream truncate">{lab.name}</span>
                      <span className="text-[12.5px] text-cream-muted">{locked ? t('a.classes.locked') : t('a.classes.active')}</span>
                      <div className="flex items-center gap-1 md:opacity-0 md:group-hover:opacity-100 transition-opacity">
                        <IconButton title={lab.is_active ? t('a.classes.lockLab') : t('a.classes.unlockLab')} onClick={() => doToggleLab(lab)}>
                          {lab.is_active ? <Icon name="lock" className="w-4 h-4" /> : <Icon name="unlock" className="w-4 h-4" />}
                        </IconButton>
                        <IconButton title={t('a.classes.forceDeleteLab')} danger onClick={() => setDeleteLab(lab)}>
                          <Icon name="trash" className="w-4 h-4" />
                        </IconButton>
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="text-cream-muted text-[14px]">{t('a.classes.noLabs')}</p>
            ))}

            {section === 'students' && (detailLoading ? <p className="text-cream-muted text-[14px]">{t('a.loading')}</p> : students.length ? (
              <div className="rounded-2xl border border-border-subtle bg-ink-raised overflow-hidden divide-y divide-border-subtle">
                {students.map((s) => (
                  <div key={s.id} className="flex items-center justify-between gap-3 px-4 py-2.5 text-[14px] hover:bg-ink-hover/40 transition-colors">
                    <span className="text-cream truncate">{s.username}</span>
                    <span className="shrink-0 text-cream-muted text-[13px]">{t('a.classes.tokensToday', { n: s.tokens_used_today.toLocaleString('en-US') })}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-cream-muted text-[14px]">{t('a.classes.noStudents')}</p>
            ))}
          </>
        )}
      </div>

      {deleteClass && (
        <ConfirmModal title={t('a.classes.forceDelete')} danger busy={busy}
          message={t('a.classes.deleteClassBody', { name: deleteClass.name })}
          confirmLabel={t('a.classes.deleteClass')} onConfirm={doDeleteClass} onCancel={() => setDeleteClass(null)} />
      )}
      {deleteLab && (
        <ConfirmModal title={t('a.classes.forceDeleteLab')} danger busy={busy}
          message={t('a.classes.deleteLabBody', { name: deleteLab.name })}
          confirmLabel={t('a.classes.deleteLab')} onConfirm={doDeleteLab} onCancel={() => setDeleteLab(null)} />
      )}
      {transferClass && (
        <Modal title={t('a.classes.transferTitle', { name: transferClass.name })} onClose={() => setTransferClass(null)}>
          <div className="space-y-4">
            <p className="text-[13px] text-cream-muted">
              {t('a.classes.ownedBy', { name: transferClass.teacher_username || t('a.classes.nobody') })}
            </p>
            <select value={transferTo} onChange={(e) => setTransferTo(e.target.value)} aria-label={t('a.classes.pickTeacher')}
              className={`w-full cursor-pointer ${inputClass}`}>
              <option value="">{t('a.classes.pickTeacher')}</option>
              {teachers.filter((u) => u.id !== transferClass.teacher_id)
                .map((u) => <option key={u.id} value={u.id}>{u.username} ({t(`a.role.${u.role}`)})</option>)}
            </select>
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => setTransferClass(null)} disabled={busy} className={`cursor-pointer ${secondaryButton}`}>{t('cancel')}</button>
              <button type="button" onClick={doTransferClass} disabled={!transferTo || busy} className={`cursor-pointer ${primaryButton}`}>
                {busy ? t('a.classes.transferring') : t('a.classes.transfer')}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

/** The teacher who owns a class; red when nobody can manage it. */
function TeacherName({ cls }) {
  const t = useT();
  if (cls.teacher_ok) return <span className="text-cream-secondary">{cls.teacher_username}</span>;
  return (
    <span className="inline-flex items-center gap-1 text-danger" title={t('a.classes.noTeacher')}>
      <Icon name="warning" className="w-3.5 h-3.5" />
      {cls.teacher_username || t('a.classes.unknown')} · {t('a.classes.noTeacherShort')}
    </span>
  );
}
