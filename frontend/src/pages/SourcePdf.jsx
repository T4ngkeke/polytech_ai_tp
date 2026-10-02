/** Open the complete source PDF at the physical page resolved by a citation. */
import { useEffect, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import api, { responseError } from '../lib/api';

export default function SourcePdf() {
  const { documentId } = useParams();
  const [searchParams] = useSearchParams();
  const sessionId = searchParams.get('session_id');
  const revisionId = searchParams.get('revision_id');
  const requestedPage = Number(searchParams.get('page'));
  const page = Number.isSafeInteger(requestedPage) && requestedPage > 0 ? requestedPage : 1;
  const [pdfUrl, setPdfUrl] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!documentId || !sessionId) return;
    const controller = new AbortController();
    let objectUrl;
    setPdfUrl(null);
    setError(null);
    const query = new URLSearchParams({ session_id: sessionId });
    if (revisionId) query.set('revision_id', revisionId);
    (async () => {
      try {
        const res = await api.raw(`/api/documents/${encodeURIComponent(documentId)}/pdf?${query}`, {
          signal: controller.signal,
        });
        if (!res.ok) throw await responseError(res);
        if (!(res.headers.get('Content-Type') || '').includes('application/pdf')) {
          throw new Error('The source is not a PDF');
        }
        const blob = await res.blob();
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setPdfUrl(objectUrl);
      } catch (err) {
        if (!controller.signal.aborted) setError(err.status === 404
          ? 'This document is no longer available to you.'
          : err.message || 'Could not open the PDF.');
      }
    })();
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [documentId, sessionId, revisionId]);

  if (!sessionId) return <p className="p-6 text-cream">Missing chat session.</p>;
  return (
    <div className="flex h-full min-h-0 flex-col text-cream">
      <div className="flex shrink-0 items-center gap-3 border-b border-border-default px-4 py-2">
        <span className="font-medium">Course PDF</span>
        <span className="text-cream-secondary">Opened at PDF page {page}. Use the PDF viewer to browse the document.</span>
      </div>
      {error ? <p role="alert" className="p-6 text-cream">{error}</p>
        : pdfUrl ? <iframe key={`${pdfUrl}#page=${page}`} title={`Course PDF, page ${page}`}
            src={`${pdfUrl}#page=${page}`} className="w-full min-h-0 flex-1 border-0" />
          : <p className="p-6 text-cream-secondary">Opening the PDF…</p>}
    </div>
  );
}
