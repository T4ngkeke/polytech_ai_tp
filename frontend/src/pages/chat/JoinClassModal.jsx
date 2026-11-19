/**
 * chat/JoinClassModal.jsx — Modal where a student enters a 6-character invite code to join a class.
 */
export default function JoinClassModal({ joinCode, isJoining, onCodeChange, onCancel, onSubmit }) {
  return (
      <div className="fixed inset-0 bg-ink-deep/80 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-fade-in">
        <div className="bg-ink-base rounded-2xl border border-border-default shadow-elevated max-w-sm w-full p-8 noise">
          <h3 className="font-display text-2xl text-cream mb-1">Join a Class</h3>
          <p className="text-sm text-cream-secondary mb-6">Enter the 6-character invite code from your teacher.</p>

          <div className="flex gap-2 justify-center mb-6">
            {joinCode.map((ch, i) => (
              <input
                key={i}
                id={`jc-${i}`}
                type="text"
                maxLength={1}
                value={ch}
                onChange={(e) => onCodeChange(i, e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Backspace' && !ch && i > 0) document.getElementById(`jc-${i - 1}`)?.focus();
                }}
                className="w-11 h-13 text-center text-lg font-mono font-bold text-cream bg-ink-deep border border-border-default rounded-lg focus:border-cyan focus:ring-1 focus:ring-cyan/30 outline-none transition-colors uppercase"
              />
            ))}
          </div>

          <div className="flex gap-3">
            <button onClick={onCancel}
              className="flex-1 py-2.5 rounded-lg border border-border-default text-cream-secondary text-sm hover:bg-ink-hover transition-all cursor-pointer">
              Cancel
            </button>
            <button
              onClick={onSubmit}
              disabled={isJoining || joinCode.join('').length !== 6}
              className="flex-1 py-2.5 rounded-lg gradient-cyan text-on-accent text-sm font-semibold disabled:opacity-50 cursor-pointer hover:brightness-110 transition-all">
              {isJoining ? 'Joining…' : 'Join Class'}
            </button>
          </div>
        </div>
      </div>
  );
}
