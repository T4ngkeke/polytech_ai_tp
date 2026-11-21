/**
 * ChatMessage.jsx — one chat message as the student sees it: the answer
 * (Markdown with [E#] links), its draft while the research agent writes, the
 * research phase, a question back with its options, the cited pages and
 * images, and the student's rating. Shared by the student chat and the
 * teacher's test drive.
 */
import { useState, useEffect, useCallback, useMemo, memo, lazy, Suspense } from 'react';
import api from '../lib/api';
import { distinctImages, groupCitations, linkCitations } from '../lib/citations';
import { t as translateNow, useT } from '../lib/i18n';
import Icon from './Icon';

// Heavy (markdown + KaTeX + highlight): loaded on demand; pages may preload it.
export const loadMessageContent = () => import('./MessageContent');
const MessageContent = lazy(loadMessageContent);

/* ── Message Bubble ── */
export function FeedbackButtons({ messageId, initial }) {
  const t = useT();
  const [value, setValue] = useState(initial || null);
  const [busy, setBusy] = useState(false);

  const rate = async (v) => {
    const next = value === v ? null : v; // click again to toggle off (visual)
    setValue(next);
    if (next === null) return;
    setBusy(true);
    try {
      await api.post(`/api/chat/messages/${messageId}/feedback`, { feedback: next });
    } catch {
      setValue(value); // revert on failure
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex gap-1.5">
      <button type="button" disabled={busy} onClick={() => rate('up')} aria-label={t('p.chat.helpful')}
        className={`rounded p-1.5 text-xs transition-colors ${
          value === 'up' ? 'bg-success-muted text-success' : 'text-cream-muted hover:bg-ink-hover'}`}>
        <Icon name="thumbsUp" className="w-3.5 h-3.5" />
      </button>
      <button type="button" disabled={busy} onClick={() => rate('down')} aria-label={t('p.chat.notHelpful')}
        className={`rounded p-1.5 text-xs transition-colors ${
          value === 'down' ? 'bg-danger-muted text-danger' : 'text-cream-muted hover:bg-ink-hover'}`}>
        <Icon name="thumbsDown" className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

// What the research agent is doing, shown in the answer bubble while it waits.
const PHASE_KEYS = { thinking: 'phase.thinking', finding: 'phase.finding',
  reading: 'phase.reading', checking_image: 'phase.image',
  checking_sources: 'phase.checking' };
const MODE_KEYS = { direct: 'p.chat.mode.direct', search: 'p.chat.mode.search', research: 'p.chat.mode.research' };

/** What the preview window needs to show citation c's page (and its file's other cited pages). */
export function sourceOf(citations, c, sessionId, focusLabels = null) {
  const sameFile = citations.filter((x) => x.document_id === c.document_id
    && (x.revision_id || null) === (c.revision_id || null));
  return { documentId: c.document_id, revisionId: c.revision_id || null,
    filename: c.filename || translateNow('p.chat.source'), sessionId, page: c.pdf_page_no ?? c.page_no,
    pages: groupCitations(sameFile)[0]?.pages || [], citations: sameFile, focusLabels };
}

export const MessageBubble = memo(function MessageBubble({ message, streaming, phase, canRate, sessionId, onOpenSource, onPick, onType, onResearch }) {
  const t = useT();
  const phaseText = PHASE_KEYS[phase] ? t(PHASE_KEYS[phase]) : null;
  const modeText = MODE_KEYS[message.answer_mode] ? t(MODE_KEYS[message.answer_mode]) : null;
  const isUser = message.sender === 'user';
  const citations = message.citations;
  // [E#] in the text (one page's labels, "E21,E22"): open those passages, highlighted on their page.
  const onCite = useCallback((ids) => {
    const labels = ids.split(',');
    const c = citations?.find((x) => x.label === labels[0]);
    if (c && c.available && sessionId) onOpenSource(sourceOf(citations, c, sessionId, labels));
  }, [citations, sessionId, onOpenSource]);
  const content = useMemo(() => {
    if (isUser || !citations?.length) return message.content;
    const usable = citations.filter((c) => c.available && c.label);
    const pageOf = new Map(usable.map((c) => [c.label,
      `${c.document_id}:${c.revision_id || ''}:${c.pdf_page_no ?? c.page_no}`]));
    return linkCitations(message.content, new Set(pageOf.keys()), pageOf);
  }, [isUser, message.content, citations]);
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} gap-2 md:gap-3 animate-fade-in`}>
      {!isUser && (
        <div className="mt-0.5 hidden h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-ink sm:flex">
          <span className="text-[13px] font-semibold text-on-ink">E</span>
        </div>
      )}
      <div className={`min-w-0 ${isUser ? 'max-w-[85%]' : 'max-w-full'} md:max-w-[72%] rounded-2xl px-3 py-2.5 md:px-4 md:py-3 text-[14px] leading-relaxed ${
        isUser
          ? 'bg-ink-hover text-cream rounded-tr-sm'
          : 'bg-ink-raised text-cream-secondary border border-border-subtle rounded-tl-sm'
      }`}>
        {message.content
          ? (isUser
              ? <span className="whitespace-pre-wrap">{message.content}</span>
              : (
                <Suspense fallback={<span className="whitespace-pre-wrap">{message.content}</span>}>
                  <MessageContent content={content} streaming={streaming} onCite={onCite} />
                </Suspense>
              ))
          // The reply as it is written, dimmed until its sources are checked.
          : streaming && message.draft ? (
            <div>
              <div className="opacity-70">
                <Suspense fallback={<span className="whitespace-pre-wrap">{message.draft}</span>}>
                  <MessageContent content={message.draft} streaming />
                </Suspense>
              </div>
              <p role="status" aria-live="polite" className="mt-2 text-xs italic text-cream-muted">
                {phaseText || t('p.chat.writing')} {t('draft.note')}
              </p>
            </div>)
          // Stopped or failed before any text: say so instead of waiting dots.
          : !streaming ? <span className="text-[13px] italic text-cream-muted">{t('answer.none')}</span>
          : <span className="inline-flex items-center gap-1">
              <BlinkDot /><BlinkDot delay="150ms" /><BlinkDot delay="300ms" />
              {phaseText && <span role="status" aria-live="polite"
                className="ml-2 text-[13px] text-cream-muted">{phaseText}</span>}
            </span>}
        {!isUser && message.clarification?.options?.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label={t('clarify.options')}>
            {message.clarification.options.map((option) => (
              <button key={option} type="button" disabled={!onPick} onClick={() => onPick?.(option)}
                className="cursor-pointer rounded-full border border-border-default px-3 py-1.5 text-[13px] text-cream transition-colors hover:bg-ink-hover disabled:cursor-default disabled:opacity-50">
                {option}
              </button>
            ))}
            {/* The answer is typed in the message box: this takes the student there. */}
            {onPick && (
              <button type="button" onClick={onType}
                className="self-center cursor-pointer text-[13px] text-cream-secondary underline hover:text-cream">
                {t('clarify.type')}
              </button>
            )}
          </div>
        )}
        {!isUser && message.citations?.length > 0 && (
          <CitationList citations={message.citations} sessionId={sessionId} onOpenSource={onOpenSource} />
        )}
        {!isUser && distinctImages(message.citations).length > 0 && (
          <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
            {distinctImages(message.citations).slice(0, 3).map((c) => (
              <VisualEvidenceCard key={c.asset_id} citation={c} sessionId={sessionId}
                onOpen={() => onOpenSource(sourceOf(message.citations, c, sessionId))} />
            ))}
          </div>
        )}
        {!isUser && onResearch && (
          <button type="button" onClick={onResearch}
            className="mt-3 cursor-pointer rounded-full border border-border-default px-3 py-1.5 text-[13px] text-cream transition-colors hover:bg-ink-hover">
            {t('use.deep')}
          </button>
        )}
        {!isUser && (canRate || modeText) && !streaming && (
          <div className="mt-2 flex items-center gap-2">
            {canRate && <FeedbackButtons messageId={message.id} initial={message.feedback} />}
            {modeText && (
              <span className="text-[12px] text-cream-muted">{modeText}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
});


export function VisualEvidenceCard({ citation, sessionId, onOpen }) {
  const t = useT();
  const [imageUrl, setImageUrl] = useState(null);
  useEffect(() => {
    if (!sessionId || !citation.available || !citation.asset_id) return;
    const controller = new AbortController();
    let objectUrl;
    const endpoint = `/api/documents/${encodeURIComponent(citation.document_id)}` +
      `/revisions/${encodeURIComponent(citation.revision_id)}` +
      `/assets/${encodeURIComponent(citation.asset_id)}?session_id=${encodeURIComponent(sessionId)}`;
    api.raw(endpoint, { signal: controller.signal }).then(async (response) => {
      if (!response.ok || !(response.headers.get('Content-Type') || '').startsWith('image/')) return;
      const blob = await response.blob();
      if (controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(blob);
      setImageUrl(objectUrl);
    }).catch(() => {});
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [citation.asset_id, citation.available, citation.document_id, citation.revision_id, sessionId]);
  if (!imageUrl) return null;
  return <button type="button" onClick={onOpen}
    title={t('p.chat.openAt', { file: citation.filename || t('p.chat.coursePdf'), page: citation.pdf_page_no })}
    className="rounded-lg border border-border-subtle p-1.5 text-left text-xs text-cream-secondary hover:border-cyan">
    <img src={imageUrl} alt={t('p.chat.imageAlt', { page: citation.pdf_page_no })}
      className="max-h-36 w-full object-contain" />
    <span>{t('p.chat.originalImage', { page: citation.pdf_page_no })}</span>
  </button>;
}

/** One line per cited file, one button per page; a button opens the preview window. */
function CitationList({ citations, sessionId, onOpenSource }) {
  const t = useT();
  const files = groupCitations(citations);
  if (!files.length) return null;
  const chip = 'inline-flex items-center rounded-md border border-cyan/20 px-1.5 py-0.5 text-[10px]';
  return (
    <div className="mt-2.5 space-y-1 border-t border-border-subtle pt-2.5">
      {files.map((file) => (
        <div key={file.key} className="flex flex-wrap items-center gap-1.5 text-[11px] text-cream-secondary">
          <DocIcon className="h-3 w-3 shrink-0 text-cyan" />
          <span className="max-w-[240px] truncate" title={file.filename}>{file.filename}</span>
          {file.newerRevision && <span className="text-cream-muted" title={t('p.chat.newerTitle')}>· {t('sources.older')}</span>}
          {file.pages.map((page) => {
            const labels = page.labels.length ? ` (${page.labels.join(', ')})` : '';
            if (!page.available || !sessionId) {
              return <span key={page.pageNo} className={`${chip} text-cream-muted`}
                title={t('sources.unavailable')}>p.{page.pageNo} · {t('p.chat.unavailable')}</span>;
            }
            return <button key={page.pageNo} type="button"
              onClick={() => onOpenSource({ ...sourceOf(citations, citations.find((c) =>
                c.document_id === file.documentId && (c.revision_id || null) === file.revisionId), sessionId),
                page: page.pageNo })}
              title={t('p.chat.openAt', { file: file.filename, page: `${page.pageNo}${labels}` })}
              className={`${chip} bg-cyan/10 hover:border-cyan hover:text-cream focus-visible:outline focus-visible:outline-cyan`}>
              p.{page.pageNo}{page.teacherRevised && <span title={t('p.chat.teacherEdited')}> ✎</span>}
            </button>;
          })}
        </div>
      ))}
    </div>
  );
}

function BlinkDot({ delay = '0ms' }) {
  return <span className="w-1.5 h-1.5 rounded-full bg-cream-muted animate-pulse inline-block" style={{ animationDelay: delay }} />;
}

function DocIcon({ className }) {
  return <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /></svg>;
}
