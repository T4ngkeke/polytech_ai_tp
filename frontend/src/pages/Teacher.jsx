/**
 * Teacher.jsx — Teacher Management page for Edu-LLM v3.
 *
 * Tab 1: Student Overview — table with usage stats, click to audit
 * Tab 2: Rule Manager — rule list with toggles + create form
 *
 * AuditModal: Read-only view of a student's chat history
 */

import { useState, useEffect } from 'react';
import toast from 'react-hot-toast';
import api from '../lib/api';

const TABS = [
  { id: 'students', label: 'Student Overview', icon: UsersIcon },
  { id: 'rules', label: 'Rule Manager', icon: RulesIcon },
];

export default function Teacher() {
  const [activeTab, setActiveTab] = useState('students');

  return (
    <div className="h-[calc(100vh-var(--header-height))] flex flex-col">
      {/* ── Tab Bar ── */}
      <div className="flex items-center gap-1 px-6 pt-5 pb-0">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`flex items-center gap-2 px-4 py-2.5 rounded-t-lg text-sm font-medium transition-all duration-200 cursor-pointer border-b-2 ${
              activeTab === tab.id
                ? 'text-accent border-accent bg-bg-surface'
                : 'text-text-secondary border-transparent hover:text-text-primary hover:bg-bg-surface-hover'
            }`}
          >
            <tab.icon className="w-4 h-4" />
            {tab.label}
          </button>
        ))}
      </div>

      {/* ── Tab Content ── */}
      <div className="flex-1 overflow-y-auto bg-bg-surface/30 border-t border-border-subtle">
        {activeTab === 'students' ? <StudentOverview /> : <RuleManager />}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════
   STUDENT OVERVIEW TAB
   ═══════════════════════════════════════════ */

function StudentOverview() {
  const [students, setStudents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [auditStudentId, setAuditStudentId] = useState(null);
  const [auditStudentName, setAuditStudentName] = useState('');

  useEffect(() => {
    loadStudents();
  }, []);

  async function loadStudents() {
    setLoading(true);
    try {
      const data = await api.get('/api/teacher/students');
      setStudents(data);
    } catch {
      toast.error('Failed to load students');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="p-6 animate-fade-in">
      <div className="flex items-center justify-between mb-5">
        <div>
          <h2 className="text-lg font-bold font-[var(--font-display)] text-text-primary">
            Active Students
          </h2>
          <p className="text-sm text-text-secondary mt-0.5">
            Monitor daily token consumption and review chat histories
          </p>
        </div>
        <button
          onClick={loadStudents}
          className="px-3 py-1.5 rounded-lg text-xs font-medium text-text-secondary hover:text-accent hover:bg-accent-muted transition-colors cursor-pointer"
        >
          ↻ Refresh
        </button>
      </div>

      {loading ? (
        <LoadingSpinner />
      ) : students.length === 0 ? (
        <EmptyState message="No active students found." />
      ) : (
        <div className="rounded-xl border border-border-subtle overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="bg-bg-elevated/50">
                <th className="text-left px-5 py-3 text-xs font-semibold uppercase tracking-wider text-text-muted">Student</th>
                <th className="text-right px-5 py-3 text-xs font-semibold uppercase tracking-wider text-text-muted">Tokens Used</th>
                <th className="text-right px-5 py-3 text-xs font-semibold uppercase tracking-wider text-text-muted">Requests</th>
                <th className="text-right px-5 py-3 text-xs font-semibold uppercase tracking-wider text-text-muted">Quota</th>
                <th className="text-right px-5 py-3 text-xs font-semibold uppercase tracking-wider text-text-muted">Usage</th>
                <th className="text-center px-5 py-3 text-xs font-semibold uppercase tracking-wider text-text-muted">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {students.map((s) => {
                const pct = s.daily_token_quota > 0
                  ? Math.min((s.tokens_used_today / s.daily_token_quota) * 100, 100)
                  : 0;
                return (
                  <tr key={s.id} className="hover:bg-bg-surface-hover/50 transition-colors">
                    <td className="px-5 py-3.5">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-full bg-accent-muted flex items-center justify-center text-xs font-bold text-accent uppercase">
                          {s.username.charAt(0)}
                        </div>
                        <span className="text-sm font-medium text-text-primary">{s.username}</span>
                      </div>
                    </td>
                    <td className="px-5 py-3.5 text-right text-sm text-text-secondary">
                      {s.tokens_used_today.toLocaleString()}
                    </td>
                    <td className="px-5 py-3.5 text-right text-sm text-text-secondary">
                      {s.request_count_today}
                    </td>
                    <td className="px-5 py-3.5 text-right text-sm text-text-muted">
                      {s.daily_token_quota.toLocaleString()}
                    </td>
                    <td className="px-5 py-3.5 text-right">
                      <div className="flex items-center gap-2 justify-end">
                        <div className="w-16 h-1.5 rounded-full bg-bg-primary overflow-hidden">
                          <div
                            className="h-full rounded-full transition-all"
                            style={{
                              width: `${pct}%`,
                              background: pct > 90 ? 'var(--color-danger)' : pct > 70 ? 'var(--color-accent-warm)' : 'var(--color-accent)',
                            }}
                          />
                        </div>
                        <span className="text-xs text-text-muted w-10 text-right">{pct.toFixed(0)}%</span>
                      </div>
                    </td>
                    <td className="px-5 py-3.5 text-center">
                      <button
                        onClick={() => { setAuditStudentId(s.id); setAuditStudentName(s.username); }}
                        className="px-3 py-1.5 rounded-lg text-xs font-medium text-accent bg-accent-muted hover:bg-accent/20 transition-colors cursor-pointer"
                      >
                        Audit
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Audit Modal */}
      {auditStudentId && (
        <AuditModal
          studentId={auditStudentId}
          studentName={auditStudentName}
          onClose={() => { setAuditStudentId(null); setAuditStudentName(''); }}
        />
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════
   AUDIT MODAL
   ═══════════════════════════════════════════ */

function AuditModal({ studentId, studentName, onClose }) {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedSession, setExpandedSession] = useState(null);

  useEffect(() => {
    loadHistory();
  }, [studentId]);

  async function loadHistory() {
    setLoading(true);
    try {
      const data = await api.get(`/api/teacher/sessions/${studentId}`);
      setSessions(data);
    } catch {
      toast.error('Failed to load student history');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" onClick={onClose}>
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />

      {/* Modal */}
      <div 
        className="relative w-full max-w-3xl max-h-[85vh] flex flex-col bg-bg-surface rounded-2xl border border-border-subtle shadow-[var(--shadow-elevated)] animate-fade-in"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border-subtle shrink-0">
          <div>
            <h3 className="text-lg font-bold font-[var(--font-display)] text-text-primary">
              Audit: {studentName}
            </h3>
            <p className="text-xs text-text-muted mt-0.5">Read-only view of all chat sessions</p>
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 rounded-lg flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-bg-surface-hover transition-colors cursor-pointer"
          >
            ✕
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-3">
          {loading ? (
            <LoadingSpinner />
          ) : sessions.length === 0 ? (
            <EmptyState message="No chat sessions found for this student." />
          ) : (
            sessions.map((session) => (
              <div key={session.id} className="rounded-xl border border-border-subtle overflow-hidden">
                {/* Session header */}
                <button
                  onClick={() => setExpandedSession(expandedSession === session.id ? null : session.id)}
                  className="w-full flex items-center justify-between px-4 py-3 bg-bg-elevated/50 hover:bg-bg-elevated transition-colors cursor-pointer"
                >
                  <div className="flex items-center gap-3">
                    <ChatBubbleIcon className="w-4 h-4 text-text-muted" />
                    <span className="text-sm font-medium text-text-primary">
                      {session.title || 'Untitled Session'}
                    </span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-xs text-text-muted">
                      {session.messages?.length || 0} messages
                    </span>
                    <span className="text-xs text-text-muted">
                      {new Date(session.created_at).toLocaleDateString()}
                    </span>
                    <ChevronIcon
                      className={`w-4 h-4 text-text-muted transition-transform ${
                        expandedSession === session.id ? 'rotate-180' : ''
                      }`}
                    />
                  </div>
                </button>

                {/* Expanded messages */}
                {expandedSession === session.id && (
                  <div className="px-4 py-3 space-y-3 bg-bg-primary/50 max-h-80 overflow-y-auto">
                    {session.messages?.length === 0 ? (
                      <p className="text-xs text-text-muted text-center py-2">No messages</p>
                    ) : (
                      session.messages?.map((msg) => (
                        <div
                          key={msg.id}
                          className={`flex ${msg.sender === 'user' ? 'justify-end' : 'justify-start'}`}
                        >
                          <div
                            className={`max-w-[80%] px-3 py-2 rounded-xl text-xs leading-relaxed whitespace-pre-wrap ${
                              msg.sender === 'user'
                                ? 'bg-accent/20 text-accent-hover rounded-br-sm'
                                : 'bg-bg-surface border border-border-subtle text-text-secondary rounded-bl-sm'
                            }`}
                          >
                            {msg.content}
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════
   RULE MANAGER TAB
   ═══════════════════════════════════════════ */

function RuleManager() {
  const [rules, setRules] = useState([]);
  const [students, setStudents] = useState([]);
  const [loading, setLoading] = useState(true);

  // Form state
  const [selectedStudent, setSelectedStudent] = useState('');
  const [rulesJson, setRulesJson] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    loadRules();
    loadStudents();
  }, []);

  async function loadRules() {
    setLoading(true);
    try {
      const data = await api.get('/api/teacher/rules');
      setRules(data);
    } catch {
      toast.error('Failed to load rules');
    } finally {
      setLoading(false);
    }
  }

  async function loadStudents() {
    try {
      const data = await api.get('/api/teacher/students');
      setStudents(data);
    } catch {
      // non-critical
    }
  }

  async function handleToggle(ruleId) {
    try {
      const result = await api.put(`/api/teacher/rules/${ruleId}/toggle`);
      setRules((prev) =>
        prev.map((r) => (r.id === ruleId ? { ...r, is_active: result.is_active } : r))
      );
      toast.success(`Rule ${result.is_active ? 'activated' : 'deactivated'}`);
    } catch {
      toast.error('Failed to toggle rule');
    }
  }

  async function handleCreateRule(e) {
    e.preventDefault();
    setSubmitting(true);
    try {
      // Validate JSON
      let parsed;
      try {
        parsed = JSON.parse(rulesJson);
      } catch {
        toast.error('Invalid JSON in rules field');
        return;
      }

      const body = {
        rules_json: parsed,
        student_id: selectedStudent || null,
        is_active: true,
      };

      await api.post('/api/teacher/rules', body);
      toast.success('Rule created successfully');
      setRulesJson('');
      setSelectedStudent('');
      loadRules();
    } catch (err) {
      toast.error(err.message || 'Failed to create rule');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="p-6 space-y-8 animate-fade-in">
      {/* ── Rule List ── */}
      <section>
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-lg font-bold font-[var(--font-display)] text-text-primary">
              Active Rules
            </h2>
            <p className="text-sm text-text-secondary mt-0.5">
              Manage pedagogical rules injected into student prompts
            </p>
          </div>
        </div>

        {loading ? (
          <LoadingSpinner />
        ) : rules.length === 0 ? (
          <EmptyState message="No rules created yet. Create one below." />
        ) : (
          <div className="space-y-2">
            {rules.map((rule) => (
              <div
                key={rule.id}
                className="flex items-start gap-4 px-5 py-4 rounded-xl border border-border-subtle bg-bg-surface hover:bg-bg-surface-hover/50 transition-colors"
              >
                {/* Toggle switch */}
                <button
                  onClick={() => handleToggle(rule.id)}
                  className={`relative shrink-0 w-10 h-6 rounded-full transition-colors duration-200 mt-0.5 cursor-pointer ${
                    rule.is_active ? 'bg-accent' : 'bg-bg-elevated'
                  }`}
                >
                  <span
                    className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow-sm transition-transform duration-200 ${
                      rule.is_active ? 'left-[18px]' : 'left-0.5'
                    }`}
                  />
                </button>

                {/* Rule details */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className={`text-xs font-semibold uppercase tracking-wider ${rule.is_active ? 'text-accent' : 'text-text-muted'}`}>
                      {rule.is_active ? 'Active' : 'Inactive'}
                    </span>
                    {rule.student_id ? (
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-medium bg-accent-warm-muted text-accent-warm">
                        Targeted
                      </span>
                    ) : (
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-medium bg-accent-muted text-accent">
                        All Students
                      </span>
                    )}
                  </div>
                  <pre className="text-xs text-text-secondary whitespace-pre-wrap break-all bg-bg-primary/50 rounded-lg px-3 py-2 mt-1 font-[var(--font-mono)]">
                    {typeof rule.rules_json === 'string' ? rule.rules_json : JSON.stringify(rule.rules_json, null, 2)}
                  </pre>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* ── Create Rule Form ── */}
      <section>
        <h2 className="text-lg font-bold font-[var(--font-display)] text-text-primary mb-4">
          Create New Rule
        </h2>
        <form onSubmit={handleCreateRule} className="rounded-xl border border-border-subtle bg-bg-surface p-5 space-y-4">
          {/* Student selector */}
          <div className="space-y-1.5">
            <label htmlFor="rule-student" className="block text-xs font-semibold uppercase tracking-wider text-text-muted">
              Target Student
            </label>
            <select
              id="rule-student"
              value={selectedStudent}
              onChange={(e) => setSelectedStudent(e.target.value)}
              className="w-full px-4 py-2.5 rounded-lg bg-bg-primary border border-border-default text-sm text-text-primary focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/30 transition-colors"
            >
              <option value="">All Students</option>
              {students.map((s) => (
                <option key={s.id} value={s.id}>{s.username}</option>
              ))}
            </select>
          </div>

          {/* Rules JSON */}
          <div className="space-y-1.5">
            <label htmlFor="rule-json" className="block text-xs font-semibold uppercase tracking-wider text-text-muted">
              Rules JSON
            </label>
            <textarea
              id="rule-json"
              value={rulesJson}
              onChange={(e) => setRulesJson(e.target.value)}
              required
              rows={6}
              placeholder='{"instruction": "Only answer in French", "restriction": "Do not provide full code solutions"}'
              className="w-full px-4 py-3 rounded-lg bg-bg-primary border border-border-default text-sm text-text-primary placeholder:text-text-muted font-[var(--font-mono)] resize-none focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/30 transition-colors"
            />
          </div>

          {/* Submit */}
          <button
            type="submit"
            disabled={submitting || !rulesJson.trim()}
            className="px-5 py-2.5 rounded-lg gradient-accent text-white text-sm font-semibold transition-all duration-200 hover:brightness-110 active:scale-[0.98] disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer shadow-[var(--shadow-glow)]"
          >
            {submitting ? 'Creating…' : 'Create Rule'}
          </button>
        </form>
      </section>
    </div>
  );
}

/* ── Shared components ── */

function LoadingSpinner() {
  return (
    <div className="flex items-center justify-center py-12">
      <div className="w-6 h-6 rounded-full border-2 border-accent border-t-transparent animate-[spin_0.8s_linear_infinite]" />
    </div>
  );
}

function EmptyState({ message }) {
  return (
    <div className="text-center py-12 text-sm text-text-muted">
      {message}
    </div>
  );
}

/* ── Icons ── */

function UsersIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M22 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" />
    </svg>
  );
}

function RulesIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="16" y1="13" x2="8" y2="13" />
      <line x1="16" y1="17" x2="8" y2="17" />
      <line x1="10" y1="9" x2="8" y2="9" />
    </svg>
  );
}

function ChatBubbleIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="m3 21 1.9-5.7a8.5 8.5 0 1 1 3.8 3.8z" />
    </svg>
  );
}

function ChevronIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}
