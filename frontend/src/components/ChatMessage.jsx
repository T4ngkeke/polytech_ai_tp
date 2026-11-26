/**
 * ChatMessage.jsx — parts of an answer that the student chat (pages/student/
 * Turns.jsx) and the teacher's views share: the rating buttons, what the
 * preview window needs for a cited page, and the card of a cited picture.
 * The whole old message bubble went with the old chat page (2026-11-26).
 */
import { useState, useEffect } from 'react';
import api from '../lib/api';
import { groupCitations } from '../lib/citations';
import { t as translateNow, useT } from '../lib/i18n';
import Icon from './Icon';

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

/** What the preview window needs to show citation c's page (and its file's other cited pages). */
export function sourceOf(citations, c, sessionId, focusLabels = null) {
  const sameFile = citations.filter((x) => x.document_id === c.document_id
    && (x.revision_id || null) === (c.revision_id || null));
  return { documentId: c.document_id, revisionId: c.revision_id || null,
    filename: c.filename || translateNow('p.chat.source'), sessionId, page: c.pdf_page_no ?? c.page_no,
    pages: groupCitations(sameFile)[0]?.pages || [], citations: sameFile, focusLabels };
}

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
