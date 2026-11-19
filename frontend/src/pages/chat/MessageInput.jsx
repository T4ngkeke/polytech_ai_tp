/**
 * chat/MessageInput.jsx — Bottom area of the chat page: notes, mode switch, message box and length counter.
 */
import Icon from '../../components/Icon';
import ModeSwitch from '../../components/ModeSwitch';

export default function MessageInput({
  resetKey, seenReset, dismissReset, blockedText, usage, askForReset, askingReset,
  activeSessionId, mode, chooseMode, examMode, examFiles, isStreaming,
  length, inputRef, input, setInput, onKeyDown, awaitingAnswer, abortRef, onSend,
}) {
  return (
    <div className="shrink-0 px-3 py-3 md:px-6 md:py-4 border-t border-border-subtle bg-ink-base/80 backdrop-blur-sm">
      {resetKey && !seenReset && (
        <div role="status" className="mb-2 flex items-center gap-3 rounded-lg border border-success/40 bg-success-muted px-3 py-2 text-xs text-cream">
          <span className="flex-1">Your teacher reset your limits. You can ask again.</span>
          <button type="button" onClick={dismissReset} aria-label="Close" title="Close"
            className="cursor-pointer rounded-md p-1 text-cream-muted hover:bg-ink-hover hover:text-cream">
            <Icon name="close" className="h-3.5 w-3.5" />
          </button>
        </div>
      )}
      {blockedText && (
        <div role="status" className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-gold/40 bg-gold-muted px-3 py-2 text-xs text-cream">
          <span className="flex-1 min-w-[12rem]">{blockedText}</span>
          {usage.reset_requested ? (
            <span className="text-cream-muted">Request sent to your teacher.</span>
          ) : usage.reset_state === 'ignored' ? null : (
            <button type="button" onClick={askForReset} disabled={askingReset}
              className="cursor-pointer rounded-md border border-border-default bg-ink-base px-2.5 py-1 text-cream hover:bg-ink-hover disabled:opacity-50">
              Ask my teacher for a reset
            </button>
          )}
        </div>
      )}
      {activeSessionId && (
        <div className="mb-2" data-tour="modes">
          <ModeSwitch mode={mode} onChange={chooseMode} examMode={examMode} examFiles={examFiles} disabled={isStreaming} />
        </div>
      )}
      <div data-tour="input" className={`flex items-end gap-3 p-3 rounded-xl border transition-colors ${
        !activeSessionId ? 'border-border-subtle opacity-50'
          : length.over ? 'border-danger' : 'border-border-default focus-within:border-cyan/40'
      } bg-ink-deep`}>
        <textarea
          ref={inputRef}
          id="chat-input"
          rows={1}
          disabled={!activeSessionId || isStreaming}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={!activeSessionId ? 'Select a session first'
            : awaitingAnswer ? 'Type your answer… (Enter to send)' : 'Type your message… (Enter to send)'}
          aria-describedby="chat-input-note"
          className="flex-1 bg-transparent text-cream text-sm resize-none outline-none placeholder:text-cream-muted max-h-32 min-h-[1.5rem] leading-relaxed"
          style={{ height: 'auto' }}
        />
        <button
          id="send-btn"
          onClick={isStreaming ? () => abortRef.current?.abort() : onSend}
          disabled={!activeSessionId || (!isStreaming && length.over)}
          className={`shrink-0 p-2 rounded-lg transition-all cursor-pointer ${
            isStreaming
              ? 'bg-danger-muted text-danger hover:bg-danger/20'
              : 'gradient-cyan text-on-accent hover:brightness-110 shadow-glow disabled:opacity-40 disabled:cursor-not-allowed'
          }`}
        >
          {isStreaming ? <Icon name="stop" className="w-4 h-4 fill-current" /> : <Icon name="send" className="w-4 h-4" />}
        </button>
      </div>
      {/* Transparency: students are told their teacher can see the chat.
          Near the length limit, the count; past it, what to do. */}
      <div id="chat-input-note" className="mt-2 flex items-start justify-center gap-3 text-[11px] text-cream-muted">
        {length.over ? (
          <p className="flex-1 text-danger" role="alert">
            Too long: {length.excess.toLocaleString('en')} characters over the limit. Shorten the message,
            or send only the part of the code that matters.
          </p>
        ) : (
          <p className={length.count ? 'flex-1' : 'text-center'}>Your teacher can see this conversation.</p>
        )}
        {length.count && (
          <span className={`shrink-0 font-mono tabular-nums ${length.over ? 'text-danger' : 'text-gold'}`}>{length.count}</span>
        )}
      </div>
    </div>
  );
}
