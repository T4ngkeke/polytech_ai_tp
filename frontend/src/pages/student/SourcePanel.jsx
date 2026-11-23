/**
 * SourcePanel.jsx — the cited page next to the chat (desktop: a right column;
 * phones: full screen). The page image with the cited passages highlighted,
 * the file's other cited pages, the full PDF, and "open in a new tab".
 * Same source object as components/PdfPreview (PdfPreviewModal).
 */
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { HighlightedPage, PdfFrame } from '../../components/PdfPreview';
import { sourceHref, useSourcePdf } from '../../lib/sourcePdf';
import Icon from '../../components/Icon';
import { useT } from '../../lib/i18n';

export default function SourcePanel({ source, onClose }) {
  const t = useT();
  const [page, setPage] = useState(source.page);
  const [mode, setMode] = useState(source.revisionId ? 'highlight' : 'pdf');
  const closeRef = useRef(null);
  const { pdfUrl, error } = useSourcePdf(mode === 'pdf' ? source.documentId : null, source.sessionId, source.revisionId);
  useEffect(() => { setPage(source.page); setMode(source.revisionId ? 'highlight' : 'pdf'); }, [source]);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);
  const pages = (source.pages || []).filter((p) => p.available !== false);
  const seg = (active) => `rounded-md px-2.5 py-1 text-[12.5px] ${active ? 'bg-ink-raised text-cream shadow-sm' : 'text-cream-secondary hover:text-cream'}`;
  return (
    <aside aria-label={`${source.filename}, ${t('sources.page', { n: page })}`}
      className="fixed inset-0 z-40 flex flex-col bg-ink-deep lg:static lg:z-auto lg:w-[44%] lg:min-w-[380px] lg:border-l lg:border-border-subtle">
      <div className="flex shrink-0 items-center gap-2 border-b border-border-subtle px-3 py-2">
        <Icon name="book" className="h-4 w-4 shrink-0 text-cyan" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[14px] font-medium text-cream" title={source.filename}>{source.filename}</div>
          <div className="text-[12px] text-cream-muted">{t('sources.page', { n: page })}</div>
        </div>
        {source.revisionId && (
          <div className="flex rounded-lg bg-ink-surface p-0.5">
            <button type="button" className={seg(mode === 'highlight')} aria-pressed={mode === 'highlight'} onClick={() => setMode('highlight')}>{t('panel.highlight')}</button>
            <button type="button" className={seg(mode === 'pdf')} aria-pressed={mode === 'pdf'} onClick={() => setMode('pdf')}>{t('panel.pdf')}</button>
          </div>
        )}
        <Link to={sourceHref({ ...source, page })} target="_blank" rel="noopener noreferrer" aria-label={t('panel.newTab')} title={t('panel.newTab')}
          className="rounded-lg p-1.5 text-cream-muted hover:bg-ink-hover hover:text-cream">
          <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"><path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /></svg>
        </Link>
        <button ref={closeRef} type="button" onClick={onClose} aria-label={t('close')}
          className="rounded-lg p-1.5 text-cream-muted hover:bg-ink-hover hover:text-cream">
          <Icon name="close" className="h-4 w-4" />
        </button>
      </div>
      {pages.length > 1 && !source.focusLabels && (
        <div className="flex shrink-0 flex-wrap gap-1.5 border-b border-border-subtle px-3 py-2">
          {pages.map((p) => (
            <button key={p.pageNo} type="button" onClick={() => setPage(p.pageNo)} aria-pressed={p.pageNo === page}
              className={`rounded-md px-1.5 text-[12px] leading-5 ${p.pageNo === page ? 'bg-cyan text-on-accent' : 'bg-ink-surface text-cream-secondary hover:bg-ink-hover hover:text-cream'}`}>
              {t('sources.page', { n: p.pageNo })}
            </button>
          ))}
        </div>
      )}
      <div className="flex min-h-0 flex-1 flex-col bg-ink-surface">
        {mode === 'highlight' ? <HighlightedPage source={source} page={page} /> : <PdfFrame pdfUrl={pdfUrl} error={error} page={page} />}
      </div>
    </aside>
  );
}
