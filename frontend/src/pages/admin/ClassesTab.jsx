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
import { lastActive, lastDays, lastWeek, sortClasses } from '../../lib/analytics';
import { plural } from '../../lib/plural';
import { PageHeader, SortableTh, IconButton, EmptyState, Spinner, StatCard, Modal, ConfirmModal } from './shared';

/* ═══════════════════════════ CLASSES TAB ═════════════════════════════════ */
// Platform numbers and every class with its teacher; a class opens on its
// Usage (the teacher Analytics view, without refill or conversations), Labs
// and Students. Replaces the old Oversight and Analytics tabs.
const CLASS_SECTIONS = [['usage', 'Usage'], ['labs', 'Labs'], ['students', 'Students']];
const NO_TEACHER = 'The teacher was deleted or is no longer a teacher: nobody can manage this class. Transfer it.';

export default function ClassesTab() {
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
    catch { toast.error('Failed to load classes'); } finally { setLoading(false); }
  }, []);

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
    ]).catch(() => toast.error('Failed to load the class')).finally(() => setDetailLoading(false));
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
      toast.success('Class deleted');
      setDeleteClass(null);
    } catch (err) { toast.error(err.message); } finally { setBusy(false); }
  };

  const doTransferClass = async () => {
    if (!transferTo) return;
    setBusy(true);
    try {
      await api.put(`/api/admin/classes/${transferClass.id}/transfer`, { teacher_id: transferTo });
      await load();               // the new teacher's name and status
      toast.success('Class transferred');
      setTransferClass(null);
    } catch (err) { toast.error(err.message); } finally { setBusy(false); }
  };

  const doDeleteLab = async () => {
    setBusy(true);
    try {
      await api.delete(`/api/teacher/labs/${deleteLab.id}`);
      setLabs((p) => p.filter((l) => l.id !== deleteLab.id));
      setClasses((p) => p.map((c) => (c.id === selectedClassId ? { ...c, lab_count: c.lab_count - 1 } : c)));
      toast.success('Lab deleted');
      setDeleteLab(null);
    } catch (err) { toast.error(err.message); } finally { setBusy(false); }
  };

  const doToggleLab = async (lab) => {
    try {
      const updated = await api.put(`/api/teacher/labs/${lab.id}`, { is_active: !lab.is_active });
      setLabs((p) => p.map((l) => (l.id === lab.id ? updated : l)));
      toast.success(updated.is_active ? 'Lab unlocked' : 'Lab locked');
    } catch (err) { toast.error(err.message); }
  };

  const transferButton = (cls) => (
    <IconButton title="Transfer to another teacher" onClick={() => { setTransferClass(cls); setTransferTo(''); }}>
      <Icon name="swap" className="w-4 h-4" />
    </IconButton>
  );

  return (
    <div className="p-4 md:p-8 overflow-y-auto h-full">
      <div className="max-w-5xl mx-auto">
        {!selectedClass ? (
          /* ── Level 0: platform numbers + class table ── */
          <>
            <PageHeader title="Classes"
              subtitle={`${plural(classes.length, 'class', 'classes')} · ${plural(teachers.length, 'teacher')}`} />
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
              <StatCard label="Active users, 7 days" value={platform?.active_users_7d} />
              <StatCard label="Questions, 7 days" value={platform ? lastWeek(days) : null} />
              <StatCard label="Questions, all time" value={platform?.total_requests?.toLocaleString()} />
              <StatCard label="Tokens, all time" value={platform?.total_tokens?.toLocaleString()} />
            </div>
            {platform && (
              <section className="rounded-xl border border-border-subtle bg-ink-raised p-4 mb-8">
                <h4 className="text-sm font-semibold text-cream mb-3">Questions per day · whole platform, last 30 days</h4>
                <DailyBars days={days} />
              </section>
            )}

            {loading ? <Spinner /> : classes.length === 0 ? (
              <EmptyState icon={<Icon name="grid" className="w-7 h-7" />} message="No classes on the platform yet." />
            ) : (
              <div className="rounded-xl border border-border-subtle bg-ink-raised overflow-x-auto">
                <table className="w-full min-w-[680px] text-sm">
                  <thead>
                    <tr className="bg-ink-surface/40 border-b border-border-subtle text-[10px] uppercase tracking-widest text-cream-muted">
                      <SortableTh label="Class" active={sort.key === 'name'} dir={sort.dir} onClick={() => toggleSort('name')} />
                      <SortableTh label="Teacher" active={sort.key === 'teacher'} dir={sort.dir} onClick={() => toggleSort('teacher')} />
                      <th className="text-left font-medium px-4 py-3">Labs</th>
                      <SortableTh label="Students" active={sort.key === 'students'} dir={sort.dir} onClick={() => toggleSort('students')} />
                      <SortableTh label="Questions, 7 d" active={sort.key === 'questions_7d'} dir={sort.dir} onClick={() => toggleSort('questions_7d')} />
                      <SortableTh label="Last question" active={sort.key === 'last_active'} dir={sort.dir} onClick={() => toggleSort('last_active')} />
                      <th className="px-4 py-3" />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((cls) => (
                      <tr key={cls.id} onClick={() => openClass(cls)}
                        className="group border-b border-border-subtle last:border-0 cursor-pointer hover:bg-ink-hover/40 transition-colors">
                        <td className="px-4 py-2.5 max-w-[240px]">
                          <span className="font-medium text-cream truncate">{cls.name}</span>
                          {cls.exam_mode && (
                            <span className="ml-2 rounded bg-gold-muted px-1.5 py-0.5 text-[10px] text-gold" title="Exam mode: answers stay in Direct mode">
                              Exam
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-2.5">
                          <TeacherName cls={cls} />
                        </td>
                        <td className="px-4 py-2.5 font-mono text-xs text-cream-secondary">{cls.lab_count}</td>
                        <td className="px-4 py-2.5 font-mono text-xs text-cream-secondary">{cls.student_count}</td>
                        <td className="px-4 py-2.5 font-mono text-xs text-cream-secondary">{cls.questions_7d || <span className="text-cream-muted">0</span>}</td>
                        <td className="px-4 py-2.5 text-xs text-cream-secondary">{lastActive(cls.last_active)}</td>
                        <td className="px-2 py-1">
                          <div className="flex items-center justify-end gap-1 md:opacity-0 md:group-hover:opacity-100 transition-opacity"
                            onClick={(e) => e.stopPropagation()}>
                            {transferButton(cls)}
                            <IconButton title="Force-delete class" danger onClick={() => setDeleteClass(cls)}>
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
            <button onClick={() => setSelectedClassId(null)}
              className="inline-flex items-center gap-1 text-xs text-cream-muted hover:text-cyan transition-colors cursor-pointer mb-4">
              <Icon name="chevronRight" className="w-3.5 h-3.5 rotate-180" /> Classes
            </button>
            <PageHeader title={selectedClass.name}
              subtitle={<>
                Teacher: <TeacherName cls={selectedClass} /> · {plural(selectedClass.lab_count, 'lab')}
                {' · '}{plural(selectedClass.student_count, 'student')}
                {' · '}Exam mode {selectedClass.exam_mode ? 'on' : 'off'}
              </>}>
              <button onClick={() => { setTransferClass(selectedClass); setTransferTo(''); }}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-border-default text-cream-secondary text-xs font-medium hover:text-cream hover:bg-ink-hover transition-colors cursor-pointer">
                <Icon name="swap" className="w-4 h-4" /> Transfer
              </button>
            </PageHeader>
            {!selectedClass.teacher_ok && (
              <div className="mb-6 flex items-center gap-2 rounded-lg border border-danger/30 bg-danger-muted px-3 py-2 text-xs text-danger">
                <Icon name="warning" className="w-4 h-4 shrink-0" /> {NO_TEACHER}
              </div>
            )}

            <div className="flex items-center gap-1 p-1 mb-6 w-fit rounded-lg bg-ink-raised border border-border-subtle">
              {CLASS_SECTIONS.map(([key, label]) => (
                <button key={key} onClick={() => setSection(key)}
                  className={`px-3 py-1 rounded-md text-xs font-medium transition-colors cursor-pointer ${
                    section === key ? 'bg-cyan-muted text-cyan' : 'text-cream-secondary hover:text-cream'
                  }`}>
                  {label}
                </button>
              ))}
            </div>

            {section === 'usage' && (
              <>
                {labs.length > 1 && (
                  <select value={usageLab} onChange={(e) => pickUsageLab(e.target.value)}
                    className="mb-4 bg-ink-surface border border-border-default text-cream-secondary text-xs px-2 py-1.5 rounded-lg cursor-pointer focus:outline-none focus:border-cyan/40">
                    <option value="">All labs</option>
                    {labs.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                  </select>
                )}
                <TeacherAnalytics data={usage} classId={selectedClass.id} refill={false} />
              </>
            )}

            {section === 'labs' && (detailLoading ? <p className="text-cream-muted text-sm">Loading…</p> : labs.length ? (
              <div className="rounded-xl border border-border-subtle bg-ink-raised overflow-hidden divide-y divide-border-subtle">
                {labs.map((lab) => {
                  const locked = lab.is_deleted || !lab.is_active;
                  return (
                    <div key={lab.id} className="group flex items-center gap-3 px-4 py-2.5">
                      <span className={`shrink-0 w-1.5 h-1.5 rounded-full ${locked ? 'bg-cream-muted' : 'bg-success'}`} />
                      <span className="flex-1 min-w-0 text-sm text-cream truncate">{lab.name}</span>
                      <span className="text-[10px] uppercase tracking-wider text-cream-muted">{locked ? 'Locked' : 'Active'}</span>
                      <div className="flex items-center gap-1 md:opacity-0 md:group-hover:opacity-100 transition-opacity">
                        <IconButton title={lab.is_active ? 'Lock lab' : 'Unlock lab'} onClick={() => doToggleLab(lab)}>
                          {lab.is_active ? <Icon name="lock" className="w-4 h-4" /> : <Icon name="unlock" className="w-4 h-4" />}
                        </IconButton>
                        <IconButton title="Force-delete lab" danger onClick={() => setDeleteLab(lab)}>
                          <Icon name="trash" className="w-4 h-4" />
                        </IconButton>
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="text-cream-muted text-sm">No labs in this class.</p>
            ))}

            {section === 'students' && (detailLoading ? <p className="text-cream-muted text-sm">Loading…</p> : students.length ? (
              <div className="rounded-xl border border-border-subtle bg-ink-raised overflow-hidden divide-y divide-border-subtle">
                {students.map((s) => (
                  <div key={s.id} className="flex items-center justify-between px-4 py-2.5 text-sm hover:bg-ink-hover/40 transition-colors">
                    <span className="text-cream">{s.username}</span>
                    <span className="text-cream-muted font-mono text-xs">{s.tokens_used_today.toLocaleString('en-US')} tokens today</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-cream-muted text-sm">No students in this class yet.</p>
            ))}
          </>
        )}
      </div>

      {deleteClass && (
        <ConfirmModal title="Force-delete class" danger busy={busy}
          message={<>Force-delete class <span className="text-cream font-medium">{deleteClass.name}</span>? This cannot be undone.</>}
          confirmLabel="Delete class" onConfirm={doDeleteClass} onCancel={() => setDeleteClass(null)} />
      )}
      {deleteLab && (
        <ConfirmModal title="Force-delete lab" danger busy={busy}
          message={<>Force-delete lab <span className="text-cream font-medium">{deleteLab.name}</span>?</>}
          confirmLabel="Delete lab" onConfirm={doDeleteLab} onCancel={() => setDeleteLab(null)} />
      )}
      {transferClass && (
        <Modal title={`Transfer "${transferClass.name}"`} onClose={() => setTransferClass(null)}>
          <div className="space-y-4">
            <p className="text-xs text-cream-muted">
              Now owned by <span className="text-cream">{transferClass.teacher_username || 'nobody'}</span>.
              Choose the teacher who will own this class.
            </p>
            <select value={transferTo} onChange={(e) => setTransferTo(e.target.value)}
              className="w-full px-4 py-3 rounded-lg bg-ink-deep border border-border-default text-cream text-sm cursor-pointer focus:outline-none focus:border-cyan/40">
              <option value="">Select a teacher…</option>
              {teachers.filter((t) => t.id !== transferClass.teacher_id)
                .map((t) => <option key={t.id} value={t.id}>{t.username} ({t.role})</option>)}
            </select>
            <button onClick={doTransferClass} disabled={!transferTo || busy}
              className="w-full py-3 rounded-lg gradient-cyan text-on-accent text-sm font-semibold hover:brightness-110 transition-all cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed">
              {busy ? 'Transferring…' : 'Transfer'}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

/** The teacher who owns a class; red when nobody can manage it. */
function TeacherName({ cls }) {
  if (cls.teacher_ok) return <span className="text-cream-secondary">{cls.teacher_username}</span>;
  return (
    <span className="inline-flex items-center gap-1 text-danger" title={NO_TEACHER}>
      <Icon name="warning" className="w-3.5 h-3.5" />
      {cls.teacher_username || 'unknown'} · no teacher
    </span>
  );
}
