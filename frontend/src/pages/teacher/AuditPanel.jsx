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
import { Answer, Question, VersionSwitch } from '../student/Turns';

/** A conversation as the student saw it (the student chat's pieces, read-only), with every version. */
function Transcript({ sessionId, messages: listed }) {
  const [chosen, setChosen] = useState({});
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
      {buildTurns(messages).map((turn) => {
        const index = chosen[turn.root] ?? turn.current;
        const { question, answer } = turn.versions[index] || turn.versions[turn.current];
        return (
          <div key={turn.root} className="space-y-3">
            {question && <Question question={question} canEdit={false}
              versions={<VersionSwitch index={index} count={turn.versions.length}
                onChange={(i) => setChosen((v) => ({ ...v, [turn.root]: i }))} />} />}
            {answer && <Answer answer={answer} live={false} isLast={false} sessionId={null}
              onOpenSource={() => {}} rateable={false} canRunCode={false} onFollowUp={() => {}} />}
          </div>
        );
      })}
    </div>
  );
}

export default function AuditPanel({ lab, sessions, students, expandedStudentId, setExpandedStudentId, expandedSession, setExpandedSession }) {
  // Three views of the same conversations: per student, which course material
  // the answers cite ("open conversation" leads back to a student), and a
  // report the model writes on the lab when the teacher asks.
  const t = useT();
  const [view, setView] = useState('students');
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
      {(!students || students.length === 0) && <p className="text-cream-muted text-[14px]">{t('students.none')}</p>}
      {(students || []).map((student) => {
        const studentSessions = sessions.filter(s => s.user_id === student.id);
        const isStudentExpanded = expandedStudentId === student.id;
        
        return (
          <div key={student.id} className="bg-ink-raised rounded-xl border border-border-subtle overflow-hidden">
            <button
              onClick={() => setExpandedStudentId(isStudentExpanded ? null : student.id)}
              className="w-full flex items-center justify-between px-4 py-3 text-left cursor-pointer hover:bg-ink-hover transition-colors"
            >
              <div>
                <p className="text-[14px] font-medium text-cream">{student.username}</p>
                <p className="text-[12.5px] text-cream-muted">{t('t.audit.sessions', { n: studentSessions.length })}</p>
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
                  <p className="text-[13px] text-cream-muted p-4">{t('t.audit.noHistory')}</p>
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
