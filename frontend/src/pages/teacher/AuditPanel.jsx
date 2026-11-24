/**
 * teacher/AuditPanel.jsx — Lab "Audit" tab: student conversations, by student, material or time.
 */
import { useCallback, useEffect, useState } from 'react';
import api from '../../lib/api';
import TeachingReport from '../../components/TeachingReport';
import CitationReport from '../../components/CitationReport';
import { useT } from '../../lib/i18n';
import QuestionSearch from './QuestionSearch';
import { buildTurns } from '../../lib/turns';
import { Answer, Question } from '../student/Turns';

/** Versions the student replaced (edit a question, ask again): kept for the audit. */
export function olderVersions(messages = []) {
  return messages.filter((m) => m.sender === 'user' && m.superseded_at).length;
}

// A fenced code block that is not a drawing (mermaid, chart): the answer gave code.
const DRAWINGS = new Set(['mermaid', 'chart']);
const hasCode = (answer) => [...(answer?.content || '').matchAll(/```([\w-]*)/g)]
  .some((fence, i) => i % 2 === 0 && !DRAWINGS.has(fence[1].toLowerCase()));
const clock = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '');

/**
 * The versions of one question, in one line whatever their number: which one
 * is shown, a stepper, first / current, and a list (one row per version: time,
 * what the student did, the question, whether the answer has code).
 */
function VersionBar({ turn, index, setIndex }) {
  const t = useT();
  const [listOpen, setListOpen] = useState(false);
  const count = turn.versions.length;
  const current = turn.current;
  const kind = (i) => {
    if (i === 0) return t('audit.versions.firstAsk');
    const same = turn.versions[i].question?.content === turn.versions[i - 1].question?.content;
    return t(same ? 'audit.versions.regen' : 'audit.versions.edit');
  };
  const button = 'rounded-full border border-gold/30 bg-ink-raised px-2.5 py-0.5 text-[12.5px] text-cream disabled:opacity-40 disabled:cursor-default cursor-pointer';
  return (
    <div className="rounded-xl border border-gold/30 bg-warn-soft px-3 py-2 text-[13px] text-gold" role="group"
      aria-label={t('audit.versions.title', { n: count })}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-semibold">{t('audit.versions.title', { n: count })}</span>
        <span>{t(index === current ? 'audit.versions.viewingCurrent' : 'audit.versions.viewingOld', { k: index + 1 })}</span>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        <button type="button" className={button} aria-label={t('version.prev')} disabled={index === 0} onClick={() => setIndex(index - 1)}>‹</button>
        <span className="min-w-[7.5em] text-center tabular-nums text-cream">
          {t('audit.versions.pos', { k: index + 1, n: count })} · {clock(turn.versions[index].question?.created_at)}
        </span>
        <button type="button" className={button} aria-label={t('version.next')} disabled={index === count - 1} onClick={() => setIndex(index + 1)}>›</button>
        <button type="button" className={button} disabled={index === 0} onClick={() => setIndex(0)}>{t('audit.versions.first')}</button>
        <button type="button" className={button} disabled={index === current} onClick={() => setIndex(current)}>{t('audit.versions.current')}</button>
        <button type="button" className="cursor-pointer px-1 underline" aria-expanded={listOpen} onClick={() => setListOpen(!listOpen)}>
          {listOpen ? t('audit.versions.hide') : t('audit.versions.list', { n: count })}
        </button>
        {turn.versions.some((v) => hasCode(v.answer)) && (
          <span className="rounded bg-danger-muted px-1.5 text-[11.5px] text-danger">{t('audit.versions.someCode')}</span>
        )}
      </div>
      {listOpen && (
        <ul className="mt-2 max-h-56 overflow-y-auto border-t border-gold/30">
          {turn.versions.map((v, i) => (
            <li key={v.question?.id || i}>
              <button type="button" onClick={() => setIndex(i)} aria-current={i === index}
                className={`grid w-full cursor-pointer grid-cols-[5.5em_3.5em_1fr_auto] items-center gap-2 border-b border-gold/30 px-1 py-1 text-left text-[12.5px] text-cream hover:bg-ink-raised sm:grid-cols-[5.5em_3.5em_9.5em_1fr_auto] ${i === index ? 'bg-ink-raised font-semibold' : ''}`}>
                <span className="whitespace-nowrap">{t('audit.versions.k', { k: i + 1 })}</span>
                <span className="whitespace-nowrap tabular-nums">{clock(v.question?.created_at)}</span>
                <span className="hidden truncate text-gold sm:inline">{kind(i)}</span>
                <span className="min-w-0 truncate text-cream-muted">{v.question?.content}</span>
                <span>{hasCode(v.answer)
                  ? <span className="rounded bg-danger-muted px-1.5 text-[11px] text-danger">{t('audit.versions.code')}</span>
                  : (i === current ? t('audit.versions.current') : '')}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** One turn of the audit: its question and answer, with its versions when the student changed it. */
function AuditTurn({ turn }) {
  const t = useT();
  const [index, setIndex] = useState(turn.current);
  const { question, answer } = turn.versions[index] || turn.versions[turn.current];
  const body = (
    <>
      {question && <Question question={question} canEdit={false} versions={null} />}
      {answer && <Answer answer={answer} live={false} isLast={false} sessionId={null}
        onOpenSource={() => {}} rateable={false} canRunCode={false} onFollowUp={() => {}} />}
    </>
  );
  if (turn.versions.length < 2) return <div className="space-y-3">{body}</div>;
  const old = index !== turn.current;
  return (
    <div className="space-y-3">
      <VersionBar turn={turn} index={index} setIndex={setIndex} />
      {old ? (
        <div className="space-y-3 border-l-[3px] border-gold/40 pl-3">
          <p className="text-[12.5px] text-gold">{t('audit.versions.replacedNote', { time: clock(question?.superseded_at) })}</p>
          {body}
        </div>
      ) : body}
    </div>
  );
}

/** A conversation as the student saw it (the student chat's pieces, read-only), with every version. */
function Transcript({ sessionId, messages: listed }) {
  // The opened conversation again, with its answers' citations (the list has none).
  const [messages, setMessages] = useState(listed);
  useEffect(() => {
    let current = true;
    api.get(`/api/teacher/chat-history?session_id=${sessionId}`)
      .then((rows) => { if (current && rows[0]) setMessages(rows[0].messages || []); }).catch(() => {});
    return () => { current = false; };
  }, [sessionId]);
  return (
    <div className="mx-auto max-w-[760px] space-y-6">
      {buildTurns(messages).map((turn) => <AuditTurn key={turn.root} turn={turn} />)}
    </div>
  );
}

export default function AuditPanel({ lab, sessions, students, expandedStudentId, setExpandedStudentId, expandedSession, setExpandedSession }) {
  // Three views of the same conversations: per student, which course material
  // the answers cite ("open conversation" leads back to a student), and a
  // report the model writes on the lab when the teacher asks.
  const t = useT();
  const [view, setView] = useState('students');
  const [onlyChanged, setOnlyChanged] = useState(false);
  const openConversation = useCallback((studentId, sessionId) => {
    setView('students');
    setExpandedStudentId(studentId);
    setExpandedSession(sessionId);
  }, [setExpandedStudentId, setExpandedSession]);
  const views = (
    <div className="flex gap-1 mb-4" role="tablist" aria-label={t('t.audit.view')}>
      {[['students', t('audit.byStudent')], ['search', t('audit.search')], ['course', t('audit.courseUse')], ['report', t('audit.report')]].map(([key, label]) => (
        <button key={key} type="button" role="tab" aria-selected={view === key} onClick={() => setView(key)}
          className={`px-3 py-1.5 rounded-lg text-[13px] cursor-pointer transition-colors ${
            view === key ? 'bg-ink-hover text-cream' : 'text-cream-muted hover:bg-ink-hover'}`}>
          {label}
        </button>
      ))}
    </div>
  );
  if (view === 'report') {
    return (
      <div className="max-w-3xl">
        <h3 className="text-[15px] font-semibold text-cream mb-4">{t('t.audit.title')}</h3>
        {views}
        <TeachingReport labId={lab.id} labName={lab.name} />
      </div>
    );
  }
  if (view === 'search') {
    return (
      <div>
        <h3 className="text-[15px] font-semibold text-cream mb-4">{t('t.audit.title')}</h3>
        {views}
        <QuestionSearch labId={lab.id} onOpenConversation={openConversation} />
      </div>
    );
  }
  if (view === 'course') {
    return (
      <div>
        <h3 className="text-[15px] font-semibold text-cream mb-4">{t('t.audit.title')}</h3>
        {views}
        <CitationReport labId={lab.id} name={lab.name} onOpenConversation={openConversation} />
      </div>
    );
  }
  return (
    <div className="space-y-3 max-w-3xl">
      <h3 className="text-[15px] font-semibold text-cream mb-4">{t('t.audit.title')}</h3>
      {views}
      {(sessions || []).some((s) => olderVersions(s.messages) > 0) && (
        <button type="button" aria-pressed={onlyChanged} onClick={() => setOnlyChanged(!onlyChanged)}
          className={`rounded-full border px-3 py-1 text-[12.5px] cursor-pointer ${onlyChanged
            ? 'border-gold/40 bg-warn-soft text-gold' : 'border-border-default bg-ink-raised text-cream-secondary hover:text-cream'}`}>
          {t('audit.versions.only')}
        </button>
      )}
      {(!students || students.length === 0) && <p className="text-cream-muted text-[14px]">{t('students.none')}</p>}
      {(students || []).map((student) => {
        const allSessions = sessions.filter(s => s.user_id === student.id);
        const changed = allSessions.filter((s) => olderVersions(s.messages) > 0).length;
        const studentSessions = onlyChanged ? allSessions.filter((s) => olderVersions(s.messages) > 0) : allSessions;
        const isStudentExpanded = expandedStudentId === student.id;
        
        return (
          <div key={student.id} className="bg-ink-raised rounded-xl border border-border-subtle overflow-hidden">
            <button
              onClick={() => setExpandedStudentId(isStudentExpanded ? null : student.id)}
              className="w-full flex items-center justify-between px-4 py-3 text-left cursor-pointer hover:bg-ink-hover transition-colors"
            >
              <div>
                <p className="text-[14px] font-medium text-cream">{student.username}</p>
                <p className="text-[12.5px] text-cream-muted">{t('t.audit.sessions', { n: allSessions.length })}
                  {changed > 0 && (
                    <span className="ml-2 rounded bg-warn-soft px-1.5 py-0.5 text-[11px] font-medium text-gold">
                      {t('audit.versions.student', { n: changed })}
                    </span>
                  )}
                </p>
              </div>
              <span className={`text-cream-muted transition-transform ${isStudentExpanded ? 'rotate-90' : ''}`}>›</span>
            </button>
            
            {isStudentExpanded && (
              <div className="border-t border-border-subtle bg-ink-base">
                {studentSessions.length > 0 && (
                  <div className="border-b border-border-subtle p-3">
                    <TeachingReport labId={lab.id} labName={lab.name} studentId={student.id} studentName={student.username} />
                  </div>
                )}
                {studentSessions.length === 0 ? (
                  <p className="text-[13px] text-cream-muted p-4">{t(onlyChanged && allSessions.length ? 'audit.versions.none' : 't.audit.noHistory')}</p>
                ) : (
                  studentSessions.map((s) => (
                    <div key={s.id} className="border-b border-border-subtle last:border-0">
                      <button
                        onClick={() => setExpandedSession(expandedSession === s.id ? null : s.id)}
                        className="w-full flex items-center justify-between px-4 py-2 text-left cursor-pointer hover:bg-ink-hover transition-colors bg-ink-deep"
                      >
                        <div>
                          <p className="text-[13px] font-medium text-cream">
                            <span className={s.is_deleted ? 'line-through opacity-70' : ''}>{s.title || t('session.untitled')}</span>
                            {s.is_deleted && (
                              <span className="ml-2 align-middle rounded bg-danger-muted px-1.5 py-0.5 text-[11px] font-medium text-danger">
                                {t('audit.deleted')}
                              </span>
                            )}
                            {olderVersions(s.messages) > 0 && (
                              <span className="ml-2 align-middle rounded bg-warn-soft px-1.5 py-0.5 text-[11px] font-medium text-gold">
                                {t('audit.versions.badge', { n: olderVersions(s.messages) })}
                              </span>
                            )}
                          </p>
                          <p className="text-[12px] text-cream-muted">{new Date(s.created_at).toLocaleString()}</p>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className="text-[12px] text-cream-muted">{t('t.audit.msgs', { n: s.messages?.length || 0 })}</span>
                          <span className={`text-cream-muted transition-transform ${expandedSession === s.id ? 'rotate-90' : ''}`}>›</span>
                        </div>
                      </button>
                      {expandedSession === s.id && (
                        <div className="max-h-[70vh] overflow-y-auto bg-ink-deep px-4 py-4">
                          <Transcript sessionId={s.id} messages={s.messages || []} />
                        </div>
                      )}
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
