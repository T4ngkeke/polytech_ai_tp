/**
 * Thread.jsx — the turns of one conversation (questions, answers, versions),
 * drawn the same way in the student chat and the teacher's test drive.
 * `chat` is what useChat returns.
 */
import { useMemo } from 'react';
import { useT } from '../../lib/i18n';
import { buildTurns } from '../../lib/turns';
import { Answer, Question, VersionSwitch } from './Turns';

export default function Thread({ chat, sessionId, features = {}, examMode = false, disabledReason = null,
  onOpenSource, inputRef, hidden = null, onNewChat = null, rate = true }) {
  const t = useT();
  const { messages, streaming, send, chosenVersion, setChosenVersion, streamedIds } = chat;
  const turns = useMemo(() => buildTurns(messages), [messages]);
  const lastTurn = turns[turns.length - 1];
  const lastQuestion = lastTurn?.versions[lastTurn.current]?.question;
  const idle = !disabledReason && !streaming;
  const again = (extra = {}) => (lastQuestion
    ? () => send(lastQuestion.content, lastQuestion.attachments || [], { versionOf: lastQuestion.id, ...extra }) : null);
  return (
    <>
      {turns.map((turn, ti) => {
        const index = chosenVersion[turn.root] ?? turn.current;
        const { question, answer } = turn.versions[index] || turn.versions[turn.current];
        const isLast = ti === turns.length - 1;
        const live = streaming && isLast && answer && !answer.finished_at;
        const showLine = hidden?.hidden > 0 && question?.id === hidden.seen_from;
        return (
          <div key={turn.root} className="space-y-4">
            {showLine && (
              <div className="flex items-center gap-3 text-[12.5px] text-cream-muted">
                <span className="h-px flex-1 bg-border-default" />
                <span>{t('history.summarized', { n: hidden.hidden })}</span>
                {onNewChat && <button type="button" onClick={onNewChat} className="underline hover:text-cream">{t('chat.new')}</button>}
                <span className="h-px flex-1 bg-border-default" />
              </div>
            )}
            {question && (
              <Question question={question} canEdit={isLast && idle}
                onEdit={(text) => send(text, question.attachments || [], { versionOf: turn.versions[turn.current].question.id })}
                versions={<VersionSwitch index={index} count={turn.versions.length}
                  onChange={(i) => setChosenVersion((v) => ({ ...v, [turn.root]: i }))} />} />
            )}
            {answer && (
              <div data-latest-answer={isLast ? '' : undefined}>
                <Answer answer={answer} live={live} isLast={isLast && index === turn.current}
                  sessionId={sessionId} onOpenSource={onOpenSource}
                  canRunCode={!!features.code && !examMode}
                  rateable={rate && !streamedIds.current.has(answer.id)}
                  onPick={isLast && !streaming ? (option) => send(option) : null}
                  onType={() => inputRef?.current?.focus()}
                  onRegenerate={idle ? again() : null}
                  onSearchCourse={idle ? () => send(t('ask.searchCourse')) : null}
                  onDeep={idle && features.deep && !examMode ? again({ toolsOverride: ['deep'] }) : null}
                  onAskAgain={idle ? again() : null}
                  onFollowUp={(text) => send(text)} />
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}
