/**
 * DocumentBlockEditor.jsx — [M2-C] teacher inspector for structured documents.
 *
 * Pick a version, walk its pages, compare each page preview with its complete
 * blocks, see sources and open problems, and change a whole block. Every change
 * names the version shown and the document generation, and creates a new draft;
 * the version shown is never changed. Nothing here publishes: a draft first
 * needs its index (M3).
 */
import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';

import api, { responseError } from '../lib/api';

const KINDS = ['heading', 'text', 'code', 'formula', 'table', 'figure', 'navigation'];
const DESCRIBABLE = new Set(['figure', 'formula', 'table']);
const STATE_LABEL = {
  queued: 'queued', processing: 'processing', parsed: 'parsed · waiting for indexing',
  ready: 'ready', partial: 'partial', failed: 'failed',
};

function useAssetUrl(url) {
  // Assets are private: fetched with the token, shown from a blob URL that is
  // released when it is no longer shown.
  const [objectUrl, setObjectUrl] = useState(null);
  useEffect(() => {
    if (!url) { setObjectUrl(null); return undefined; }
    let alive = true;
    let created = null;
    api.raw(url).then(async (res) => {
      if (!res.ok) throw await responseError(res);
      created = URL.createObjectURL(await res.blob());
      if (alive) setObjectUrl(created); else URL.revokeObjectURL(created);
    }).catch(() => alive && setObjectUrl(null));
    return () => { alive = false; if (created) URL.revokeObjectURL(created); };
  }, [url]);
  return objectUrl;
}

export default function StructuredDocumentDetail({ document: doc, onBack, onRefresh }) {
  const [versions, setVersions] = useState(null);
  const [revisionId, setRevisionId] = useState(null);
  const [outline, setOutline] = useState([]);
  const [outlineCursor, setOutlineCursor] = useState(null);
  const [pageId, setPageId] = useState(null);
  const [page, setPage] = useState(null);
  const [busy, setBusy] = useState(false);

  const loadVersions = useCallback(async (select) => {
    const data = await api.get(`/api/teacher/documents/${doc.id}/revisions`);
    setVersions(data);
    const newest = data.revisions[data.revisions.length - 1];
    setRevisionId(select || data.current_revision_id || newest?.id || null);
    return data;
  }, [doc.id]);

  useEffect(() => { loadVersions().catch((e) => toast.error(e.message)); }, [loadVersions]);

  const revision = versions?.revisions.find((r) => r.id === revisionId);
  const hasContent = revision && ['parsed', 'ready', 'partial'].includes(revision.processing_state);
  const base = `/api/teacher/documents/${doc.id}/revisions/${revisionId}`;

  useEffect(() => {
    setOutline([]); setOutlineCursor(null); setPage(null);
    if (!revisionId || !hasContent) return;
    api.get(`${base}/outline`).then((res) => {
      setOutline(res.items); setOutlineCursor(res.next_cursor);
      // Page ids of the same source stay the same across edits: keep the page open.
      setPageId((prev) => (res.items.some((p) => p.page_id === prev) ? prev : res.items[0]?.page_id || null));
    }).catch((e) => toast.error(e.message));
  }, [revisionId, hasContent, base]);

  const loadPage = useCallback(async () => {
    if (!pageId || !revisionId) return;
    setPage(await api.get(`${base}/pages/${pageId}`));
  }, [base, pageId, revisionId]);

  useEffect(() => { loadPage().catch((e) => toast.error(e.message)); }, [loadPage]);

  const moreOutline = async () => {
    const res = await api.get(`${base}/outline?cursor=${encodeURIComponent(outlineCursor)}`);
    setOutline((prev) => [...prev, ...res.items]); setOutlineCursor(res.next_cursor);
  };

  // Run one change; on success show the new draft it created.
  const change = async (label, fn) => {
    setBusy(true);
    try {
      const out = await fn({ expected_revision_id: revisionId, expected_generation: versions.generation });
      const newId = out?.revision_id || out?.revision?.revision_id;
      toast.success(newId ? `${label}: new draft created` : `${label}: saved`);
      await loadVersions(newId || revisionId);
      if (!newId) await loadPage();
      onRefresh?.().catch(() => {});
      return true;
    } catch (err) {
      toast.error(err.status === 409
        ? `${err.message} — someone changed this document; the view was reloaded.`
        : err.message);
      if (err.status === 409) await loadVersions(revisionId).catch(() => {});
      return false;
    } finally {
      setBusy(false);
    }
  };

  const reprocess = () => change('Reprocess', (target) =>
    api.post(`/api/teacher/documents/${doc.id}/revisions/${revisionId}/reprocess`,
      { expected_generation: target.expected_generation }));

  const downloadSource = async () => {
    const res = await api.raw(`${base}/source`);
    if (!res.ok) { toast.error((await responseError(res)).message); return; }
    const url = URL.createObjectURL(await res.blob());
    const link = Object.assign(window.document.createElement('a'), { href: url, download: doc.filename });
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-4">
      <button type="button" onClick={onBack} className="text-sm text-cyan hover:underline">← Back to documents</button>
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border-default p-3">
        <h3 className="truncate font-medium text-cream">{doc.filename}</h3>
        {versions && (
          <select value={revisionId || ''} onChange={(e) => setRevisionId(e.target.value)}
            className="rounded border border-border-default bg-ink-deep px-2 py-1 text-xs text-cream">
            {versions.revisions.map((r, i) => (
              <option key={r.id} value={r.id}>
                v{i + 1} · {r.reason} · {STATE_LABEL[r.processing_state] || r.processing_state} · {r.publication_state}
                {r.id === versions.current_revision_id ? ' (published)' : ''}
              </option>
            ))}
          </select>
        )}
        <button type="button" disabled={busy || !revision} onClick={reprocess}
          className="rounded border border-border-default px-2 py-1 text-xs text-cream-secondary hover:bg-ink-hover disabled:opacity-50">
          Reprocess file
        </button>
        <button type="button" disabled={!revision} onClick={downloadSource}
          className="rounded border border-border-default px-2 py-1 text-xs text-cream-secondary hover:bg-ink-hover disabled:opacity-50">
          Download original
        </button>
      </div>
      {revision && <RevisionSummary revision={revision} />}
      {revision && !hasContent && (
        <p className="text-sm text-cream-muted">
          {revision.processing_state === 'failed'
            ? `This version failed (${revision.report?.error_code || 'unknown error'}). Reprocess to try again.`
            : 'This version is still being processed.'}
        </p>
      )}
      {hasContent && (
        <div className="grid gap-4 md:grid-cols-[14rem_1fr]">
          <nav className="max-h-[70vh] space-y-1 overflow-y-auto">
            {outline.map((p) => (
              <button key={p.page_id} type="button" onClick={() => setPageId(p.page_id)}
                className={`block w-full rounded px-2 py-1 text-left text-xs ${p.page_id === pageId ? 'bg-ink-hover text-cream' : 'text-cream-secondary hover:bg-ink-hover'}`}>
                <span className="font-medium">Slide {p.source_slide_no}</span>
                {p.state !== 'usable' && <span className="ml-1 text-gold">⚠</span>}
                <span className="block truncate text-cream-muted">{p.title || p.summary || '—'}</span>
              </button>
            ))}
            {outlineCursor && (
              <button type="button" onClick={moreOutline} className="text-xs text-cyan hover:underline">More pages…</button>
            )}
          </nav>
          {page && <PageView page={page} base={base} busy={busy} change={change} docId={doc.id} />}
        </div>
      )}
    </div>
  );
}

function RevisionSummary({ revision }) {
  const report = revision.report || {};
  const notes = [];
  if (report.parse_quality) notes.push(`parse quality: ${report.parse_quality}`);
  if (report.visible_slide_count != null) notes.push(`${report.visible_slide_count} visible slides`);
  if (report.hidden_slide_count) notes.push(`${report.hidden_slide_count} hidden slides left out`);
  if (report.carried_forward) {
    notes.push(`teacher changes carried over: ${report.carried_forward.carried}, to check: ${report.carried_forward.reviews}`);
  }
  return (
    <p className="text-xs text-cream-muted">
      {STATE_LABEL[revision.processing_state] || revision.processing_state} · {revision.publication_state}
      {notes.length > 0 && ` · ${notes.join(' · ')}`}
      {revision.processing_state === 'parsed' && ' · cannot be published before indexing'}
    </p>
  );
}

function PageView({ page, base, busy, change, docId }) {
  const preview = useAssetUrl(page.page.preview_asset_id ? `${base}/assets/${page.page.preview_asset_id}` : null);
  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2 text-sm text-cream">
        <span className="font-medium">Slide {page.page.source_slide_no}</span>
        <span className="text-xs text-cream-muted">
          {page.page.preview_mapping === 'verified'
            ? `preview page ${page.page.preview_page_no}`
            : 'preview page not verified'}
          {' · '}{page.page.state}
        </span>
      </div>
      {preview
        ? <img src={preview} alt={`Preview of slide ${page.page.source_slide_no}`} className="max-h-96 rounded border border-border-default" />
        : <p className="rounded bg-ink-surface p-2 text-xs text-cream-muted">No verified preview for this slide.</p>}
      {page.unavailable.length > 0 && (
        <ul className="space-y-1 rounded bg-gold-muted p-2 text-xs text-gold">
          {page.unavailable.map((u, i) => <li key={i}>⚠ {u.message}{u.type ? ` (${u.type})` : ''}</li>)}
        </ul>
      )}
      {page.reviews?.map((r) => <ReviewCard key={r.id} review={r} busy={busy} change={change} docId={docId} base={base} />)}
      <ul className="space-y-2">
        {page.blocks.map((b) => <BlockCard key={b.block_id} block={b} busy={busy} change={change} docId={docId} base={base} />)}
      </ul>
    </section>
  );
}

function ReviewCard({ review, busy, change, base }) {
  const [text, setText] = useState(review.candidate_content || '');
  const resolve = (status) => change(status === 'confirmed' ? 'Confirm' : 'Ignore', (target) =>
    api.put(`${base}/reviews/${review.id}`, {
      expected_generation: target.expected_generation, review_revision: review.revision, status,
      ...(status === 'confirmed' ? { content: text } : {}),
    }));
  return (
    <div className="rounded border border-gold/40 p-2 text-xs">
      <p className="text-gold">
        {review.kind === 'vision_description' ? `Model description (${review.model}) — check it against the picture` : review.reason}
      </p>
      {review.uncertainties?.length > 0 && <p className="text-cream-muted">Model not sure about: {review.uncertainties.join('; ')}</p>}
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3}
        className="mt-1 w-full rounded border border-border-default bg-ink-deep p-1 font-mono text-cream" />
      <div className="mt-1 flex gap-2">
        <button type="button" disabled={busy || !text.trim()} onClick={() => resolve('confirmed')} className="text-cyan hover:underline disabled:opacity-50">Use it</button>
        <button type="button" disabled={busy} onClick={() => resolve('ignored')} className="text-cream-muted hover:underline disabled:opacity-50">Ignore</button>
      </div>
    </div>
  );
}

function BlockCard({ block, busy, change, docId }) {
  const [editing, setEditing] = useState(false);
  const [content, setContent] = useState(block.content);
  const [kind, setKind] = useState(block.type);
  const [description, setDescription] = useState(block.content_description || '');
  const url = `/api/teacher/documents/${docId}/blocks/${block.block_id}`;
  const excluded = block.index_state === 'excluded';

  const save = async () => {
    const body = {};
    if (content !== block.content) body.content = content;
    if (kind !== block.type) body.kind = kind;
    if (description !== (block.content_description || '')) {
      if (description.trim()) body.content_description = description; else body.clear_description = true;
    }
    if (Object.keys(body).length === 0) { setEditing(false); return; }
    // The typed text is kept if the save fails.
    if (await change('Edit', (target) => api.put(url, { ...target, ...body }))) setEditing(false);
  };

  return (
    <li className={`rounded border p-2 ${excluded ? 'border-dashed border-border-default opacity-70' : 'border-border-default'}`}>
      <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-cream-muted">
        <span className="font-medium text-cream-secondary">{block.type}</span>
        <span>{block.source_completeness}</span>
        <span>{block.index_state}</span>
        {block.edited_by_teacher && <span className="text-cyan">teacher revised</span>}
        {block.description_origin !== 'none' && <span>description: {block.description_origin}</span>}
        <span className="ml-auto flex gap-2">
          {!editing && <button type="button" disabled={busy} onClick={() => setEditing(true)} className="text-cyan hover:underline">Edit</button>}
          <button type="button" disabled={busy}
            onClick={() => change(excluded ? 'Include' : 'Exclude', (target) => api.post(`${url}/exclude`, { ...target, excluded: !excluded }))}
            className="hover:underline">{excluded ? 'Index again' : 'No indexing needed'}</button>
          {DESCRIBABLE.has(block.type) && (
            <button type="button" disabled={busy}
              onClick={() => change('Describe', (target) => api.post(`${url}/describe`, { expected_revision_id: target.expected_revision_id }))}
              className="hover:underline">Ask model to describe</button>
          )}
        </span>
      </div>
      {editing ? (
        <div className="space-y-2">
          <select value={kind} onChange={(e) => setKind(e.target.value)} className="rounded border border-border-default bg-ink-deep px-2 py-1 text-xs text-cream">
            {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
          <textarea value={content} onChange={(e) => setContent(e.target.value)}
            rows={Math.min(16, Math.max(3, content.split('\n').length + 1))}
            className="w-full resize-y rounded border border-border-default bg-ink-deep p-2 font-mono text-sm text-cream" />
          <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={400}
            placeholder="What this block is for (optional, max 400 characters)"
            className="w-full rounded border border-border-default bg-ink-deep p-1 text-xs text-cream" />
          <div className="flex gap-2">
            <button type="button" onClick={save} disabled={busy} className="rounded gradient-cyan px-3 py-1 text-xs font-medium text-cream disabled:opacity-50">Save as new draft</button>
            <button type="button" disabled={busy} onClick={() => { setEditing(false); setContent(block.content); setKind(block.type); setDescription(block.content_description || ''); }}
              className="rounded border border-border-default px-3 py-1 text-xs text-cream-secondary">Cancel</button>
          </div>
        </div>
      ) : (
        <pre className="whitespace-pre-wrap break-words font-mono text-sm text-cream-secondary">{block.content || '—'}</pre>
      )}
      {block.content_description && !editing && <p className="mt-1 text-xs italic text-cyan">{block.content_description}</p>}
      {block.edited_by_teacher && block.source_text != null && block.source_text !== block.content && (
        <details className="mt-1 text-xs text-cream-muted"><summary>Native text</summary>
          <pre className="whitespace-pre-wrap font-mono">{block.source_text}</pre></details>
      )}
      {block.issues.length > 0 && (
        <ul className="mt-1 text-xs text-gold">{block.issues.map((i, n) => <li key={n}>⚠ {i.message}</li>)}</ul>
      )}
      <p className="mt-1 text-[10px] text-cream-muted">source: {block.source.extraction_method} · {block.source.native_object_ids.join(', ')}</p>
    </li>
  );
}
