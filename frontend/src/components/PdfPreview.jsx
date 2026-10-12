/** The course PDF of a citation: an inline frame and the preview window over the chat. */
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { sourceHref, useSourcePdf } from '../lib/sourcePdf';

/** The browser's PDF viewer at one page; the PDF is loaded once per document. */
export function PdfFrame({ pdfUrl, error, page, className = '' }) {
  if (error) return <p role="alert" className="p-6 text-cream">{error}</p>;
  if (!pdfUrl) return <p className="p-6 text-cream-secondary">Opening the PDF…</p>;
  // A new key reloads the viewer at the page (only the fragment changes).
  return <iframe key={`${pdfUrl}#page=${page}`} title={`Course PDF, page ${page}`}
    src={`${pdfUrl}#page=${page}`} className={`w-full min-h-0 flex-1 border-0 ${className}`} />;
}

/**
 * A window over the chat showing a cited page. source: { documentId,
 * revisionId, filename, sessionId, page, pages: [{ pageNo, labels }] } — the
 * other cited pages of the same file can be shown without closing.
 */
export function PdfPreviewModal({ source, onClose }) {
  const [page, setPage] = useState(source.page);
  const closeRef = useRef(null);
  const { pdfUrl, error } = useSourcePdf(source.documentId, source.sessionId, source.revisionId);
  useEffect(() => setPage(source.page), [source]);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);
  const pages = (source.pages || []).filter((p) => p.available !== false);
  const labels = pages.find((p) => p.pageNo === page)?.labels || [];
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div role="dialog" aria-modal="true" aria-label={`${source.filename}, page ${page}`}
        className="flex h-[85vh] w-[min(1000px,94vw)] flex-col overflow-hidden rounded-xl border border-border-default bg-ink-base shadow-2xl">
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border-subtle px-4 py-2 text-sm text-cream">
          <span className="max-w-[45%] truncate font-medium" title={source.filename}>{source.filename}</span>
          <span className="text-cream-secondary">· PDF page {page}</span>
          {labels.length > 0 && <span className="text-xs text-cream-muted">[{labels.join(', ')}]</span>}
          {pages.length > 1 && (
            <span className="ml-2 flex flex-wrap gap-1">
              {pages.map((p) => (
                <button key={p.pageNo} type="button" onClick={() => setPage(p.pageNo)}
                  aria-pressed={p.pageNo === page}
                  className={`rounded px-1.5 py-0.5 text-xs ${p.pageNo === page
                    ? 'bg-cyan text-ink-deep' : 'bg-cyan-muted/60 text-cream-secondary hover:text-cream'}`}>
                  p.{p.pageNo}
                </button>
              ))}
            </span>
          )}
          <span className="ml-auto flex items-center gap-2">
            <Link to={sourceHref({ ...source, page })} target="_blank" rel="noopener noreferrer"
              className="text-xs text-cream-secondary hover:text-cyan">Open in a new tab</Link>
            <button ref={closeRef} type="button" onClick={onClose} aria-label="Close"
              className="rounded px-2 py-0.5 text-cream-secondary hover:bg-ink-hover hover:text-cream">✕</button>
          </span>
        </div>
        <PdfFrame pdfUrl={pdfUrl} error={error} page={page} />
      </div>
    </div>
  );
}
