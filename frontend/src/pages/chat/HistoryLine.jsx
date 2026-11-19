/**
 * chat/HistoryLine.jsx — The line above which the assistant no longer sees the messages (lib/historyLine.js).
 */

export default function HistoryLine({ onNewSession }) {
  return (
    // Phones: the text fills the width, so the dashed rule goes above it.
    <div role="note" className="flex items-center justify-center gap-3 mb-5 pt-2 sm:pt-1 pb-1 text-xs text-cream-muted
      border-t border-dashed border-border-default sm:border-t-0">
      <span className="hidden sm:block flex-1 border-t border-dashed border-border-default" />
      <p className="max-w-md text-center leading-relaxed">
        <span className="font-semibold text-cream-secondary">The assistant no longer sees the messages above this line.</span>
        <br />
        Paste again what it still needs, or{' '}
        <button type="button" onClick={onNewSession} className="cursor-pointer text-cyan underline hover:brightness-110">
          start a new conversation
        </button>.
      </p>
      <span className="hidden sm:block flex-1 border-t border-dashed border-border-default" />
    </div>
  );
}
