/** The course PDF of a citation: an inline frame and the preview window over the chat. */
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { pageHighlights } from '../lib/citations';
import { sourceHref, usePageImage, useSourcePdf } from '../lib/sourcePdf';

/** The browser's PDF viewer at one page; the PDF is loaded once per document. */
export function PdfFrame({ pdfUrl, error, page, className = '' }) {
  if (error) return <p role="alert" className="p-6 text-cream">{error}</p>;
  if (!pdfUrl) return <p className="p-6 text-cream-secondary">Opening the PDF…</p>;
  // A new key reloads the viewer at the page (only the fragment changes).
  return <iframe key={`${pdfUrl}#page=${page}`} title={`Course PDF, page ${page}`}
    src={`${pdfUrl}#page=${page}`} className={`w-full min-h-0 flex-1 border-0 ${className}`} />;
}

/** A page image with the cited regions marked like a highlighter; a click hides them. */
function HighlightedPage({ source, page }) {
  const { imageUrl, error } = usePageImage(source.documentId, source.revisionId, page, source.sessionId);
  const [shown, setShown] = useState(true);
  const scrollRef = useRef(null);
  const boxes = pageHighlights(source.citations, page, source.focusLabel);
  useEffect(() => setShown(true), [page, source]);
  if (error) return <p role="alert" className="p-6 text-cream">{error}</p>;
  if (!imageUrl) return <p className="p-6 text-cream-secondary">Opening the page…</p>;
  const scrollToFirst = (e) => {
    // Bring the first marked passage into view (pages can be taller than the window).
    if (boxes.length && scrollRef.current) {
      scrollRef.current.scrollTop = Math.max(0, e.currentTarget.offsetHeight * boxes[0].box[1] - 40);
    }
  };
  return (
    <div className="relative min-h-0 flex-1">
      {!shown && boxes.length > 0 && (
        <button type="button" onClick={() => setShown(true)}
          className="absolute right-4 top-3 z-10 rounded-md bg-yellow-300 px-2 py-1 text-xs font-medium text-neutral-900 shadow">
          Show highlights
        </button>
      )}
      <div ref={scrollRef} className="h-full overflow-auto bg-ink-deep p-3">
        <div className="relative mx-auto w-fit max-w-full cursor-default" onClick={() => setShown(false)}>
          <img src={imageUrl} alt={`${source.filename}, page ${page}`} onLoad={scrollToFirst}
            className="block h-auto max-w-full select-none" draggable={false} />
          {shown && boxes.map(({ box, labels }) => (
            <div key={box.join(',')} aria-hidden="true"
              className="pointer-events-none absolute rounded-sm border-2 border-yellow-400 bg-yellow-300/30 animate-fade-in"
              style={{ left: `${box[0] * 100}%`, top: `${box[1] * 100}%`,
                width: `${(box[2] - box[0]) * 100}%`, height: `${(box[3] - box[1]) * 100}%` }}>
              {labels.length > 0 && (
                <span className="absolute -top-2.5 left-1 rounded bg-yellow-400 px-1 text-[10px] font-semibold leading-4 text-neutral-900">
                  {labels.join(', ')}
                </span>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/**
 * A window over the chat showing a cited page. source: { documentId,
 * revisionId, filename, sessionId, page, pages: [{ pageNo, labels }],
 * citations (this file's, with their regions), focusLabel (one [E#] only) }.
 * It opens on the page image with the cited passages highlighted; "Full PDF"
 * switches to the browser's PDF viewer.
 */
export function PdfPreviewModal({ source, onClose }) {
  const [page, setPage] = useState(source.page);
  // A revision is needed for the page image; legacy citations show the PDF.
  const [mode, setMode] = useState(source.revisionId ? 'highlight' : 'pdf');
  const closeRef = useRef(null);
  // The whole PDF is downloaded only for the full viewer.
  const { pdfUrl, error } = useSourcePdf(mode === 'pdf' ? source.documentId : null,
    source.sessionId, source.revisionId);
  useEffect(() => {
    setPage(source.page);
    setMode(source.revisionId ? 'highlight' : 'pdf');
  }, [source]);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);
  const pages = (source.pages || []).filter((p) => p.available !== false);
  const labels = source.focusLabel ? [source.focusLabel]
    : pages.find((p) => p.pageNo === page)?.labels || [];
  const tab = (active) => `rounded px-2 py-0.5 text-xs ${active
    ? 'bg-cyan text-on-accent' : 'text-cream-secondary hover:bg-ink-hover hover:text-on-accent'}`;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div role="dialog" aria-modal="true" aria-label={`${source.filename}, page ${page}`}
        className="flex h-[85vh] w-[min(1000px,94vw)] flex-col overflow-hidden rounded-xl border border-border-default bg-ink-base shadow-2xl">
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border-subtle px-4 py-2 text-sm text-cream">
          <span className="max-w-[40%] truncate font-medium" title={source.filename}>{source.filename}</span>
          <span className="text-cream-secondary">· PDF page {page}</span>
          {labels.length > 0 && <span className="text-xs text-cream-muted">[{labels.join(', ')}]</span>}
          {pages.length > 1 && !source.focusLabel && (
            <span className="ml-2 flex flex-wrap gap-1">
              {pages.map((p) => (
                <button key={p.pageNo} type="button" onClick={() => setPage(p.pageNo)}
                  aria-pressed={p.pageNo === page}
                  className={`rounded px-1.5 py-0.5 text-xs ${p.pageNo === page
                    ? 'bg-cyan text-on-accent' : 'bg-cyan/10 text-cream-secondary hover:text-on-accent'}`}>
                  p.{p.pageNo}
                </button>
              ))}
            </span>
          )}
          <span className="ml-auto flex items-center gap-1">
            {source.revisionId && <>
              <button type="button" className={tab(mode === 'highlight')} aria-pressed={mode === 'highlight'}
                onClick={() => setMode('highlight')}>Highlights</button>
              <button type="button" className={tab(mode === 'pdf')} aria-pressed={mode === 'pdf'}
                onClick={() => setMode('pdf')}>Full PDF</button>
            </>}
            <Link to={sourceHref({ ...source, page })} target="_blank" rel="noopener noreferrer"
              className="ml-2 text-xs text-cream-secondary hover:text-cyan">Open in a new tab</Link>
            <button ref={closeRef} type="button" onClick={onClose} aria-label="Close"
              className="ml-1 rounded px-2 py-0.5 text-cream-secondary hover:bg-ink-hover hover:text-cream">✕</button>
          </span>
        </div>
        {mode === 'highlight'
          ? <HighlightedPage source={source} page={page} />
          : <PdfFrame pdfUrl={pdfUrl} error={error} page={page} />}
      </div>
    </div>
  );
}
