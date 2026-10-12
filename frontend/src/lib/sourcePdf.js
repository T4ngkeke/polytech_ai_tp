/** Loading a cited course PDF, and the link to open it in its own tab. */
import { useEffect, useState } from 'react';
import api, { responseError } from './api';

/** Load the PDF of a document revision (authorized by the chat session) as a blob URL. */
export function useSourcePdf(documentId, sessionId, revisionId) {
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
  return { pdfUrl, error };
}

export function sourceHref({ documentId, revisionId, sessionId, page }) {
  const query = new URLSearchParams({ session_id: sessionId, page: String(page) });
  if (revisionId) query.set('revision_id', revisionId);
  return `/source/${encodeURIComponent(documentId)}?${query}`;
}

/** One page of a revision as an image (for the highlight view), as a blob URL. */
export function usePageImage(documentId, revisionId, page, sessionId) {
  const [imageUrl, setImageUrl] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (!documentId || !revisionId || !sessionId || !page) return;
    const controller = new AbortController();
    let objectUrl;
    setImageUrl(null);
    setError(null);
    const url = `/api/documents/${encodeURIComponent(documentId)}/revisions/${encodeURIComponent(revisionId)}`
      + `/pages/${encodeURIComponent(page)}/image?session_id=${encodeURIComponent(sessionId)}`;
    api.raw(url, { signal: controller.signal }).then(async (res) => {
      if (!res.ok) throw await responseError(res);
      const blob = await res.blob();
      if (controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(blob);
      setImageUrl(objectUrl);
    }).catch((err) => {
      if (!controller.signal.aborted) setError(err.status === 404
        ? 'This page is no longer available to you.' : 'Could not show the page.');
    });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [documentId, revisionId, page, sessionId]);
  return { imageUrl, error };
}
