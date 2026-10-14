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

// Heavy (markdown + KaTeX + highlight): loaded on demand; pages may preload it.
export const loadMessageContent = () => import('./MessageContent');
const MessageContent = lazy(loadMessageContent);

/* ── Message Bubble ── */
function FeedbackButtons({ messageId, initial }) {
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
    <div className="mt-2 flex gap-1.5">
      <button type="button" disabled={busy} onClick={() => rate('up')} aria-label="Helpful"
        className={`rounded px-1.5 py-0.5 text-xs transition-colors ${
          value === 'up' ? 'bg-emerald-500/20 text-emerald-300' : 'text-cream-muted hover:bg-ink-hover'}`}>
        👍
      </button>
      <button type="button" disabled={busy} onClick={() => rate('down')} aria-label="Not helpful"
        className={`rounded px-1.5 py-0.5 text-xs transition-colors ${
          value === 'down' ? 'bg-danger-muted text-danger' : 'text-cream-muted hover:bg-ink-hover'}`}>
        👎
      </button>
    </div>
  );
}

// What the research agent is doing, shown in the answer bubble while it waits.
const PHASE_LABELS = { thinking: 'Thinking…', finding: 'Searching course material…',
  reading: 'Reading source pages…', checking_image: 'Checking the image…',
  checking_sources: 'Checking citations…' };

/** What the preview window needs to show citation c's page (and its file's other cited pages). */
function sourceOf(citations, c, sessionId, focusLabel = null) {
  const sameFile = citations.filter((x) => x.document_id === c.document_id
    && (x.revision_id || null) === (c.revision_id || null));
  return { documentId: c.document_id, revisionId: c.revision_id || null,
    filename: c.filename || 'Source', sessionId, page: c.pdf_page_no ?? c.page_no,
    pages: groupCitations(sameFile)[0]?.pages || [], citations: sameFile, focusLabel };
}

export const MessageBubble = memo(function MessageBubble({ message, streaming, phase, canRate, sessionId, onOpenSource, onPick }) {
  const isUser = message.sender === 'user';
  const citations = message.citations;
  // [E#] in the text: open that passage alone, highlighted on its page.
  const onCite = useCallback((label) => {
    const c = citations?.find((x) => x.label === label);
    if (c && c.available && sessionId) onOpenSource(sourceOf(citations, c, sessionId, label));
  }, [citations, sessionId, onOpenSource]);
  const content = useMemo(() => (isUser || !citations?.length ? message.content
    : linkCitations(message.content, new Set(citations.filter((c) => c.available && c.label).map((c) => c.label)))),
  [isUser, message.content, citations]);
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} gap-3 animate-fade-in`}>
      {!isUser && (
        <div className="w-7 h-7 rounded-lg gradient-cyan flex items-center justify-center shrink-0 mt-0.5 shadow-glow">
          <span className="text-cream text-[10px] font-bold">AI</span>
        </div>
      )}
      <div className={`max-w-[72%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${
        isUser
          ? 'bg-cyan-muted text-cream rounded-tr-sm border border-cyan/20'
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
                {PHASE_LABELS[phase] || 'Writing…'} Sources are checked when the answer is complete.
              </p>
            </div>)
          // Stopped or failed before any text: say so instead of waiting dots.
          : !streaming ? <span className="text-xs italic text-cream-muted">No answer: the research stopped before it finished. You can ask again.</span>
          : <span className="inline-flex items-center gap-1">
              <BlinkDot /><BlinkDot delay="150ms" /><BlinkDot delay="300ms" />
              {PHASE_LABELS[phase] && <span role="status" aria-live="polite"
                className="ml-2 text-xs text-cream-muted">{PHASE_LABELS[phase]}</span>}
            </span>}
        {!isUser && message.clarification?.options?.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label="Possible answers">
            {message.clarification.options.map((option) => (
              <button key={option} type="button" disabled={!onPick} onClick={() => onPick?.(option)}
                className="px-3 py-1.5 rounded-lg border border-cyan/30 text-xs text-cream hover:bg-cyan-muted disabled:opacity-50 disabled:cursor-default cursor-pointer transition-colors">
                {option}
              </button>
            ))}
            {onPick && <span className="self-center text-[11px] text-cream-muted">or type your answer</span>}
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
        {!isUser && !streaming && canRate && (
          <FeedbackButtons messageId={message.id} initial={message.feedback} />
        )}
      </div>
    </div>
  );
});


function VisualEvidenceCard({ citation, sessionId, onOpen }) {
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
    title={`Open ${citation.filename || 'course PDF'} at PDF page ${citation.pdf_page_no}`}
    className="rounded-lg border border-border-subtle p-1.5 text-left text-xs text-cream-secondary hover:border-cyan">
    <img src={imageUrl} alt={`Original course image on PDF page ${citation.pdf_page_no}`}
      className="max-h-36 w-full object-contain" />
    <span>Original image · p.{citation.pdf_page_no}</span>
  </button>;
}

/** One line per cited file, one button per page; a button opens the preview window. */
function CitationList({ citations, sessionId, onOpenSource }) {
  const files = groupCitations(citations);
  if (!files.length) return null;
  const chip = 'inline-flex items-center rounded-md border border-cyan/20 px-1.5 py-0.5 text-[10px]';
  return (
    <div className="mt-2.5 space-y-1 border-t border-border-subtle pt-2.5">
      {files.map((file) => (
        <div key={file.key} className="flex flex-wrap items-center gap-1.5 text-[11px] text-cream-secondary">
          <DocIcon className="h-3 w-3 shrink-0 text-cyan" />
          <span className="max-w-[240px] truncate" title={file.filename}>{file.filename}</span>
          {file.newerRevision && <span className="text-cream-muted" title="A newer course revision exists">· older version</span>}
          {file.pages.map((page) => {
            const labels = page.labels.length ? ` (${page.labels.join(', ')})` : '';
            if (!page.available || !sessionId) {
              return <span key={page.pageNo} className={`${chip} text-cream-muted`}
                title="Source no longer available">p.{page.pageNo} · unavailable</span>;
            }
            return <button key={page.pageNo} type="button"
              onClick={() => onOpenSource({ ...sourceOf(citations, citations.find((c) =>
                c.document_id === file.documentId && (c.revision_id || null) === file.revisionId), sessionId),
                page: page.pageNo })}
              title={`Open ${file.filename} at PDF page ${page.pageNo}${labels}`}
              className={`${chip} bg-cyan-muted/60 hover:border-cyan hover:text-cream focus-visible:outline focus-visible:outline-cyan`}>
              p.{page.pageNo}{page.teacherRevised && <span title="Teacher edited this source"> ✎</span>}
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
