/**
 * Direct / Search / Research, next to the chat input. In exam mode the
 * teacher has turned course search off: only Direct is shown, with the reason.
 */
import { MODES } from '../lib/answerModes';

export default function ModeSwitch({ mode, onChange, examMode = false, disabled = false }) {
  if (examMode) {
    return (
      <p className="text-[11px] text-gold" role="status">
        Exam mode: your teacher has turned course search off. Answers are Direct only.
      </p>
    );
  }
  const current = MODES.find((m) => m.key === mode) || MODES[2];
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <div role="radiogroup" aria-label="Answer mode"
        className="inline-flex rounded-lg border border-border-default bg-ink-deep p-0.5">
        {MODES.map((m) => (
          <button key={m.key} type="button" role="radio" aria-checked={m.key === mode}
            disabled={disabled} title={m.hint} onClick={() => onChange(m.key)}
            className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors cursor-pointer disabled:cursor-not-allowed ${
              m.key === mode ? 'bg-cyan-muted text-cyan' : 'text-cream-secondary hover:text-cream'}`}>
            {m.label}
          </button>
        ))}
      </div>
      <span className="text-[11px] text-cream-muted">{current.hint}</span>
    </div>
  );
}
