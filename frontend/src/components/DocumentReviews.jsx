import { useEffect, useState } from 'react';
import api, { responseError } from '../lib/api';

const reviewsPath = (documentId) => `/api/teacher/documents/${documentId}/reviews`;

// What a failed save means, and what the teacher can do next. `blocks` lists
// the actions that cannot help until something else changes.
function describeFailure(err) {
  if (err.status === 409 && err.code === 'review_source_conflict') {
    return { kind: 'source_conflict', blocks: ['confirmed'],
      text: 'This area no longer matches the indexed text, so nothing was overwritten. Your text is kept.' };
  }
  if (err.status === 409 && err.code === 'review_source_changed') {
    return { kind: 'source_changed', blocks: ['confirmed', 'ignored'],
      text: 'The original file changed or is missing. Nothing was saved. Upload the file again.' };
  }
  if (err.status === 409) {
    return { kind: 'stale', blocks: ['confirmed', 'ignored'],
      text: 'Someone changed this review. Your text is kept. Reload the review, check it, then submit again.' };
  }
  if (err.status === 503 || err.code === 'embedding_unavailable') {
    return { kind: 'retry', blocks: [],
      text: 'The search index service is unavailable. Nothing was saved. Your text is kept; try again.' };
  }
  return { kind: 'retry', blocks: [], text: 'Nothing was saved. Your text is kept; try again.' };
}

export default function DocumentReviews({ documentId, onResolved }) {
  const [rows, setRows] = useState([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError('');
    api.get(reviewsPath(documentId))
      .then((items) => { if (alive) setRows(items); })
      .catch((err) => { if (alive) setError(err.message); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [documentId, attempt]);
  const reloadList = () => setAttempt((n) => n + 1);
  if (error) {
    return <div role="alert" className="space-y-1 text-sm">
      <p>Could not load visual reviews: {error}</p>
      <button type="button" className="text-cyan" onClick={reloadList}>Retry loading reviews</button>
    </div>;
  }
  if (loading && !rows.length) return <p className="text-sm text-cream-muted">Loading visual reviews…</p>;
  if (!rows.length) return null;
  const replace = (updated) => setRows((previous) => previous.map((r) => r.id === updated.id ? updated : r));
  return <section className="space-y-3">
    <h4 className="font-semibold">Visual reviews ({rows.filter((r) => r.status === 'pending').length} pending)</h4>
    {rows.map((row) => <Review key={row.id} row={row} documentId={documentId}
      onReloaded={replace}
      onSaved={(updated) => { replace(updated); reloadList(); onResolved?.(); }} />)}
  </section>;
}

function ReviewImage({ endpoint, row }) {
  const [attempt, setAttempt] = useState(0);
  const [image, setImage] = useState({ state: 'loading', url: '', message: '' });
  useEffect(() => {
    // Never keep the previous picture on screen while another one loads.
    setImage({ state: 'loading', url: '', message: '' });
    let alive = true;
    let url;
    api.raw(`${endpoint}/image`).then(async (res) => {
      if (!res.ok) throw await responseError(res);
      const blob = await res.blob();
      url = URL.createObjectURL(blob);
      if (alive) setImage({ state: 'ready', url, message: '' });
      else URL.revokeObjectURL(url);
    }).catch((err) => {
      if (alive) setImage({ state: 'error', url: '', message: err.code === 'review_source_changed'
        ? 'The original file changed or is missing, so this picture cannot be shown.' : err.message });
    });
    return () => { alive = false; if (url) URL.revokeObjectURL(url); };
  }, [endpoint, row.revision, attempt]);
  if (image.state === 'loading') return <p className="text-sm text-cream-muted">Loading source image…</p>;
  if (image.state === 'error') {
    return <div role="alert" className="text-sm">
      <p>Source image unavailable: {image.message}</p>
      <button type="button" className="text-cyan" onClick={() => setAttempt((n) => n + 1)}>Retry image</button>
    </div>;
  }
  return <img src={image.url} alt={`Source region on page ${row.page_no}`} className="max-h-96 max-w-full bg-white" />;
}

function Review({ row, documentId, onSaved, onReloaded }) {
  const endpoint = `${reviewsPath(documentId)}/${row.id}`;
  const [content, setContent] = useState(row.final_content || '');
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const [showImage, setShowImage] = useState(false);
  const blocked = (status) => busy || Boolean(failure?.blocks.includes(status));
  const save = async (status) => {
    setBusy(true); setFailure(null);
    try {
      const updated = await api.put(endpoint, { revision: row.revision, status, content: status === 'confirmed' ? content : null });
      onSaved(updated);
    } catch (err) {
      setFailure({ ...describeFailure(err), detail: err.message });
    } finally { setBusy(false); }
  };
  const reload = async () => {
    setBusy(true);
    try {
      const rows = await api.get(reviewsPath(documentId));
      const updated = rows.find((r) => r.id === row.id);
      if (!updated) throw new Error('Review no longer available');
      onReloaded(updated); setFailure(null);
    } catch (err) { setFailure((previous) => ({ ...previous, detail: err.message })); }
    finally { setBusy(false); }
  };
  return <article className="rounded border border-border-default p-3 space-y-2">
    <p className="text-sm">Page {row.page_no} · {row.status === 'pending' ? 'Needs review' : row.status === 'confirmed' ? 'Confirmed' : 'No indexing needed'}</p>
    <p>{row.reason}</p>
    {showImage ? <ReviewImage key={row.id} endpoint={endpoint} row={row} />
      : <button type="button" className="text-cyan text-sm" onClick={() => setShowImage(true)}>View source image</button>}
    {row.candidate_content && <div className="text-sm text-cream-muted"><p>Unconfirmed suggestion (not searchable):</p><pre className="whitespace-pre-wrap">{row.candidate_content}</pre></div>}
    {row.status === 'pending' ? <>
      <label className="block text-sm">Confirmed content
        <textarea value={content} onChange={(e) => setContent(e.target.value)} maxLength={20000} rows={4}
          className="block w-full rounded border border-border-default bg-ink-deep p-2 text-cream" />
      </label>
      <p className="text-xs text-cream-muted">Describe what the picture shows. The original text of this area is kept automatically; you do not need to retype it.</p>
      <div className="flex gap-3 text-sm">
        <button type="button" disabled={blocked('confirmed') || !content.trim()} onClick={() => save('confirmed')} className="text-cyan disabled:opacity-40">Confirm &amp; index</button>
        <button type="button" disabled={blocked('ignored')} onClick={() => save('ignored')} className="text-cyan disabled:opacity-40">No indexing needed</button>
      </div>
    </> : row.final_content && <pre className="whitespace-pre-wrap">{row.final_content}</pre>}
    {failure && <div role="alert" className="text-danger text-sm">
      <p>{failure.text}</p>
      {failure.detail && <p className="text-xs">{failure.detail}</p>}
    </div>}
    {failure?.kind === 'stale' && <button type="button" disabled={busy} onClick={reload} className="text-cyan">Reload review</button>}
  </article>;
}
