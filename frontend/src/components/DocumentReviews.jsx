import { useEffect, useState } from 'react';
import api from '../lib/api';

export default function DocumentReviews({ documentId, onResolved }) {
  const [rows, setRows] = useState([]);
  const [error, setError] = useState('');
  useEffect(() => {
    let alive = true;
    api.get(`/api/teacher/documents/${documentId}/reviews`)
      .then((items) => { if (alive) setRows(items); })
      .catch((err) => { if (alive) setError(err.message); });
    return () => { alive = false; };
  }, [documentId]);
  if (error) return <p role="alert">Could not load visual reviews: {error}</p>;
  if (!rows.length) return null;
  return <section className="space-y-3">
    <h4 className="font-semibold">Visual reviews ({rows.filter((r) => r.status === 'pending').length} pending)</h4>
    {rows.map((row) => <Review key={row.id} row={row} documentId={documentId} onChange={(updated, resolved) => {
      setRows((previous) => previous.map((r) => r.id === updated.id ? updated : r));
      if (resolved) onResolved?.();
    }} />)}
  </section>;
}

function Review({ row, documentId, onChange }) {
  const endpoint = `/api/teacher/documents/${documentId}/reviews/${row.id}`;
  const [content, setContent] = useState(row.final_content || '');
  const [error, setError] = useState('');
  const [stale, setStale] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showImage, setShowImage] = useState(false);
  const [image, setImage] = useState('');
  const [imageError, setImageError] = useState('');
  useEffect(() => {
    if (!showImage) return;
    let alive = true;
    let url;
    setImageError('');
    api.raw(`${endpoint}/image`).then(async (res) => {
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Image unavailable (${res.status})`);
      }
      const blob = await res.blob();
      if (alive) { url = URL.createObjectURL(blob); setImage(url); }
    }).catch((err) => { if (alive) setImageError(err.message); });
    return () => { alive = false; if (url) URL.revokeObjectURL(url); };
  }, [endpoint, showImage, row.revision]);
  const save = async (status) => {
    setBusy(true); setError('');
    try {
      const updated = await api.put(endpoint, { revision: row.revision, status, content: status === 'confirmed' ? content : null });
      onChange(updated, true);
    } catch (err) {
      setError(err.message); setStale(err.status === 409);
    } finally { setBusy(false); }
  };
  const reload = async () => {
    setBusy(true);
    try {
      const rows = await api.get(`/api/teacher/documents/${documentId}/reviews`);
      const updated = rows.find((r) => r.id === row.id);
      if (!updated) throw new Error('Review no longer available');
      onChange(updated, false); setStale(false); setError('');
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };
  return <article className="rounded border border-border-default p-3 space-y-2">
    <p className="text-sm">Page {row.page_no} · {row.status === 'pending' ? 'Needs review' : row.status === 'confirmed' ? 'Confirmed' : 'No indexing needed'}</p>
    <p>{row.reason}</p>
    <button type="button" className="text-cyan text-sm" onClick={() => setShowImage(true)}>View source image</button>
    {image && <img src={image} alt={`Source region on page ${row.page_no}`} className="max-h-96 max-w-full bg-white" />}
    {imageError && <p role="alert">{imageError}</p>}
    {row.candidate_content && <div className="text-sm text-cream-muted"><p>Unconfirmed suggestion (not searchable):</p><pre className="whitespace-pre-wrap">{row.candidate_content}</pre></div>}
    {row.status === 'pending' ? <>
      <label className="block text-sm">Confirmed content
        <textarea value={content} onChange={(e) => setContent(e.target.value)} maxLength={20000} rows={4}
          className="block w-full rounded border border-border-default bg-ink-deep p-2 text-cream" />
      </label>
      <div className="flex gap-3 text-sm">
        <button type="button" disabled={busy || stale || !content.trim()} onClick={() => save('confirmed')} className="text-cyan disabled:opacity-40">Confirm &amp; index</button>
        <button type="button" disabled={busy || stale} onClick={() => save('ignored')} className="text-cyan disabled:opacity-40">No indexing needed</button>
      </div>
    </> : row.final_content && <pre className="whitespace-pre-wrap">{row.final_content}</pre>}
    {error && <p role="alert" className="text-danger">{error}</p>}
    {stale && <><p className="text-sm">Your text is kept. Reload and check the latest review before submitting again.</p>
      <button type="button" disabled={busy} onClick={reload} className="text-cyan">Reload review</button></>}
  </article>;
}
