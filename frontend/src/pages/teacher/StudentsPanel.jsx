/**
 * teacher/StudentsPanel.jsx — Lab "Students" tab: enrolled students, kick, per-student rule.
 */


export default function StudentsPanel({ students, onKick, onSetRule }) {
  if (students.length === 0) return <p className="text-cream-muted text-sm">No students enrolled yet.</p>;
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold text-cream mb-4">Enrolled Students ({students.length})</h3>
      {students.map((s) => (
        <div key={s.id} className="flex items-center justify-between px-4 py-3 rounded-xl bg-ink-raised border border-border-subtle">
          <div>
            <p className="text-sm font-medium text-cream">{s.username}</p>
            <p className="text-xs text-cream-muted font-mono">{s.role}</p>
          </div>
          <div className="flex gap-2">
            <button onClick={() => onSetRule(s)} className="text-xs text-cyan hover:bg-cyan-muted px-3 py-1 rounded-lg transition-colors cursor-pointer border border-cyan/20">
              Student rules
            </button>
            <button onClick={() => onKick(s.id)} className="text-xs text-danger hover:bg-danger-muted px-3 py-1 rounded-lg transition-colors cursor-pointer border border-transparent">
              Kick
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
