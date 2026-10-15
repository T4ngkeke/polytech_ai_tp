/**
 * DocumentManager.jsx — Teacher ingestion visibility.
 *
 * The document author is the ground-truth oracle for "was this processed well?",
 * so this surface lets a teacher:
 *   - upload course material (PDF / Markdown / HTML),
 *   - see each document's ingestion status + summary (pages / chunks),
 *   - open a document to read the indexed chunks (the inspector),
 *   - edit a chunk (re-embeds + re-indexes) to fix a chunking mistake.
 *
 * The upload form used to ask for a type (CM / TD-TP / corrigé) and an audience.
 * Both are gone: everything a teacher uploads is course material the whole class
 * can read. So are the exercise editor, the hint review lifecycle and the
 * segmentation compare-and-confirm panel — there are no exercise rows to review.
 *
 * Props:
 *   labId: string — the lab whose documents are shown (required)
 *
 * Backed by:
 *   GET    /api/teacher/labs/{labId}/documents
 *   POST   /api/teacher/labs/{labId}/documents     (multipart: file, language, shared)
 *   GET    /api/teacher/documents/{id}/chunks
 *   PUT    /api/teacher/documents/{id}/chunks/{chunkId}   (edit + re-index)
 *   DELETE /api/teacher/documents/{id}
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../lib/api';
import DocumentReviews from './DocumentReviews';
import StructuredDocumentDetail from './DocumentBlockEditor';
import { ingestReportWarnings } from '../lib/ingestReport';

// Document language selects the BM25 tsvector config; ingest and query sides
// must match. fr is the backend default.
const LANGUAGES = [{ value: 'fr', label: 'Français' }, { value: 'en', label: 'English' }];

// Status → badge classes. Coloured chips read fine on the dark panel.
const STATUS_BADGE = {
  pending: 'bg-ink-surface text-cream-muted',
  processing: 'bg-cyan-muted text-cyan',
  indexed: 'bg-success-muted text-success',
  partially_indexed: 'bg-gold-muted text-gold',
  failed: 'bg-danger-muted text-danger',
  needs_review: 'bg-gold-muted text-gold',
  parsed: 'bg-cyan-muted text-cyan',
};

const STATUS_LABEL = { parsed: 'parsed · waiting for indexing' };

const IN_PROGRESS = new Set(['pending', 'processing']);

// A structured document has no published version until indexing
// exists (M3). Until then its newest draft tells what is going on.
const REVISION_STATUS = { queued: 'pending', processing: 'processing', parsed: 'parsed', failed: 'failed' };
function displayStatus(doc) {
  if (doc.structured && !doc.current_revision_id && doc.latest_revision) {
    return REVISION_STATUS[doc.latest_revision.processing_state] || doc.latest_revision.processing_state;
  }
  return doc.status;
}

export default function DocumentManager({ labId }) {
  const [documents, setDocuments] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(null); // document summary being inspected
  const [uploading, setUploading] = useState(false);

  const fileRef = useRef(null);
  const [shared, setShared] = useState(false);             // class-wide material
  const [language, setLanguage] = useState('fr');          // BM25 tsvector config

  const loadDocuments = useCallback(async () => {
    if (!labId) return [];
    const rows = await api.get(`/api/teacher/labs/${labId}/documents`);
    setDocuments(rows);
    return rows;
  }, [labId]);

  // Initial load when the lab changes.
  useEffect(() => {
    if (!labId) return;
    setSelected(null);
    setLoading(true);
    loadDocuments()
      .catch((e) => toast.error(e.message))
      .finally(() => setLoading(false));
  }, [labId, loadDocuments]);

  // Poll only while something is still being ingested (pending → indexed).
  useEffect(() => {
    if (!documents.some((d) => IN_PROGRESS.has(displayStatus(d)))) return;
    const id = setInterval(() => {
      loadDocuments().catch(() => {});
    }, 3000);
    return () => clearInterval(id);
  }, [documents, loadDocuments]);

  const handleUpload = async (e) => {
    e.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file) {
      toast.error('Choose a file first.');
      return;
    }
    const form = new FormData();
    form.append('file', file);
    form.append('language', language);
    if (shared) form.append('shared', 'true');

    setUploading(true);
    try {
      await api.upload(`/api/teacher/labs/${labId}/documents`, form);
      if (fileRef.current) fileRef.current.value = '';
      toast.success('Uploaded — ingestion queued.');
      await loadDocuments();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setUploading(false);
    }
  };

  const deleteDocument = async (doc, e) => {
    e.stopPropagation();
    if (!window.confirm(
      `Delete "${doc.filename}" and everything indexed from it? This cannot be undone.`
    )) return;
    try {
      // A structured document is deleted with the generation the teacher saw.
      await api.delete(`/api/teacher/documents/${doc.id}${doc.structured ? `?expected_generation=${doc.generation}` : ''}`);
      toast.success('Document deleted');
      if (selected?.id === doc.id) setSelected(null);
      await loadDocuments();
    } catch (err) {
      toast.error(err.message);
    }
  };

  if (!labId) {
    return <p className="text-sm text-cream-muted">Select a lab to manage its documents.</p>;
  }

  if (selected) {
    const Detail = selected.structured ? StructuredDocumentDetail : DocumentDetail;
    return (
      <Detail
        document={selected}
        onRefresh={async () => {
          const rows = await loadDocuments();
          const updated = rows.find((row) => row.id === selected.id);
          if (updated) setSelected(updated);
        }}
        onBack={() => setSelected(null)}
      />
    );
  }

  return (
    <div className="space-y-4">
      <form onSubmit={handleUpload} className="flex flex-wrap items-end gap-3 rounded-lg border border-border-default bg-ink-deep/30 p-3">
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-cream-muted">Course material</label>
          <input ref={fileRef} type="file" accept="application/pdf,.pdf,text/markdown,.md,text/html,.html,.htm" className="max-w-full text-sm text-cream-secondary file:mr-2 file:rounded file:border-0 file:bg-ink-surface file:px-2 file:py-1 file:text-cream-secondary" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-cream-muted">Language</label>
          <select value={language} onChange={(e) => setLanguage(e.target.value)} className="rounded border border-border-default bg-ink-deep px-2 py-1 text-sm text-cream focus:border-cyan/40 focus:outline-none">
            {LANGUAGES.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
          </select>
        </div>
        <label className="flex items-center gap-1.5 pb-1.5 text-xs text-cream-muted">
          <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)}
            className="accent-cyan" />
          Share class-wide
        </label>
        <button
          type="submit"
          disabled={uploading}
          className="rounded gradient-cyan px-3 py-1.5 text-sm font-medium text-on-accent hover:brightness-110 disabled:opacity-50"
        >
          {uploading ? 'Uploading…' : 'Upload'}
        </button>
      </form>

      {loading ? (
        <p className="text-sm text-cream-muted">Loading documents…</p>
      ) : documents.length === 0 ? (
        <p className="text-sm text-cream-muted">No documents yet. Upload course material above.</p>
      ) : (
        <ul className="divide-y divide-border-subtle rounded-lg border border-border-default">
          {documents.map((doc) => (
            <li key={doc.id} className="flex items-stretch hover:bg-ink-hover">
              <button
                type="button"
                onClick={() => setSelected(doc)}
                className="flex min-w-0 flex-1 flex-col items-start gap-1 px-3 py-2 text-left sm:flex-row sm:items-center sm:justify-between sm:gap-3"
              >
                <span className="flex min-w-0 max-w-full items-center gap-2">
                  <span className="truncate text-sm font-medium text-cream">{doc.filename}</span>
                  {(doc.status === 'needs_review' || doc.status === 'failed' || doc.status === 'partially_indexed') && (
                    <span title={doc.error_message || ''} className="text-gold">⚠</span>
                  )}
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  <StatusBadge status={displayStatus(doc)} />
                  <SummaryChips doc={doc} />
                </span>
              </button>
              <button
                type="button"
                onClick={(e) => deleteDocument(doc, e)}
                title="Delete document"
                aria-label={`Delete ${doc.filename}`}
                className="shrink-0 px-3 text-cream-muted hover:text-danger"
              >
                🗑
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function StatusBadge({ status }) {
  const cls = STATUS_BADGE[status] || 'bg-ink-surface text-cream-muted';
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium ${cls}`}>
      {STATUS_LABEL[status] || status.replace('_', ' ')}
    </span>
  );
}

// Current usable state (index_state) when present; older reports only have the
// processing history (page_summary).
function currentPages(report) {
  const state = report?.index_state;
  if (state) {
    return { usable: state.available_pages.length, unavailable: state.missing_pages.length,
      noIndex: state.no_index_pages.length, pending: state.pending_review_count };
  }
  const pages = report?.page_summary;
  if (!pages) return null;
  return { usable: pages.total - pages.missing.length, unavailable: pages.missing.length, noIndex: 0,
    pending: report?.review_summary?.pending };
}

function SummaryChips({ doc }) {
  const pages = currentPages(doc.ingest_report);
  return (
    <span className="hidden gap-1 text-xs text-cream-muted sm:flex">
      <span title="pages">📄 {doc.page_count ?? '–'}</span>
      <span title="chunks">🧩 {doc.chunk_count}</span>
      {pages && <span>{pages.usable} usable · {pages.unavailable} unavailable{pages.noIndex ? ` · ${pages.noIndex} not indexed` : ''}</span>}
      {pages?.pending != null && <span>{pages.pending} visual reviews pending</span>}
    </span>
  );
}

function IngestReport({ report }) {
  const warnings = ingestReportWarnings(report);
  const summary = report?.page_summary;
  const state = report?.index_state;
  if (state && state.status !== 'indexed') {
    warnings.push(`Now: ${state.available_pages.length} pages usable, ${state.missing_pages.length} unavailable, ${state.no_index_pages.length} marked "no indexing needed", ${state.pending_review_count} visual reviews pending.`);
  }
  if (summary) {
    warnings.push(`Last processing run, ${summary.total} pages: ${summary.updated.length} updated, ${summary.retained.length} kept from the previous run, ${summary.missing.length} unavailable.`);
    for (const page of report?.page_reader?.page_results || []) {
      if (page.status !== 'updated') warnings.push(`Page ${page.page_no}: ${page.status}. ${page.error || ''}`);
    }
  }
  if (warnings.length === 0) return null;
  return (
    <ul className="mt-2 space-y-1 rounded bg-gold-muted p-2 text-xs text-gold">
      {warnings.map((w, i) => <li key={i}>⚠ {w}</li>)}
    </ul>
  );
}

function DocumentDetail({ document: doc, onBack, onRefresh }) {
  const [chunks, setChunks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [reviewRevision, setReviewRevision] = useState(0);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api.get(`/api/teacher/documents/${doc.id}/chunks`)
      .then((c) => { if (alive) setChunks(c); })
      .catch((err) => toast.error(err.message))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [doc.id, reviewRevision]);

  return (
    <div className="space-y-4">
      <button type="button" onClick={onBack} className="text-sm text-cyan hover:underline">
        ← Back to documents
      </button>

      <div className="rounded-lg border border-border-default p-3">
        <div className="flex items-center gap-2">
          <h3 className="truncate font-medium text-cream">{doc.filename}</h3>
          <StatusBadge status={doc.status} />
        </div>
        <p className="mt-1 text-xs text-cream-muted">
          {doc.page_count ?? '–'} pages · {doc.chunk_count} chunks
        </p>
        {doc.error_message && (
          <p className="mt-2 rounded bg-gold-muted p-2 text-xs text-gold">{doc.error_message}</p>
        )}
        <IngestReport report={doc.ingest_report} />
      </div>

      <DocumentReviews key={doc.id} documentId={doc.id} onResolved={() => {
        setReviewRevision((v) => v + 1);
        onRefresh().catch((err) => toast.error(`Saved, but document summary could not refresh: ${err.message}`));
      }} />

      {loading ? (
        <p className="text-sm text-cream-muted">Loading processing report…</p>
      ) : (
        <section>
          <h4 className="mb-2 text-sm font-semibold text-cream-secondary">
            Chunk inspector ({chunks.length})
          </h4>
          {chunks.length === 0 ? (
            <p className="text-sm text-cream-muted">No chunks indexed for this document.</p>
          ) : (
            <ul className="space-y-2">
              {chunks.map((c) => (
                <EditableChunk
                  key={c.id}
                  docId={doc.id}
                  chunk={c}
                  onSaved={(updated) => setChunks((prev) =>
                    prev.map((x) => (x.id === updated.id ? updated : x)))}
                />
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}

function EditableChunk({ docId, chunk, onSaved }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(chunk.content);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!value.trim()) { toast.error('Chunk text cannot be empty.'); return; }
    setSaving(true);
    try {
      const updated = await api.put(`/api/teacher/documents/${docId}/chunks/${chunk.id}`, { content: value });
      onSaved(updated);
      setEditing(false);
      toast.success('Chunk updated & re-indexed');
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSaving(false);
    }
  };

  const cancel = () => { setValue(chunk.content); setEditing(false); };

  return (
    <li className="rounded border border-border-default p-2">
      <div className="mb-1 flex items-center justify-between">
        <p className="text-xs text-cream-muted">
          #{chunk.chunk_index}
          {chunk.page_no != null && ` · p.${chunk.page_no}`}
          {chunk.section && ` · ${chunk.section}`}
        </p>
        {!editing && (
          <button type="button" onClick={() => setEditing(true)} className="text-xs text-cyan hover:underline">
            Edit
          </button>
        )}
      </div>
      {chunk.context && (
        <p className="mb-1 rounded bg-cyan-muted p-1.5 text-xs italic text-cyan">
          context: {chunk.context}
        </p>
      )}
      {editing ? (
        <div className="space-y-2">
          <textarea
            value={value}
            onChange={(e) => setValue(e.target.value)}
            rows={Math.min(16, Math.max(3, value.split('\n').length + 1))}
            className="w-full resize-y rounded border border-border-default bg-ink-deep p-2 font-mono text-sm text-cream focus:border-cyan/40 focus:outline-none"
          />
          <div className="flex gap-2">
            <button type="button" onClick={save} disabled={saving}
              className="rounded gradient-cyan px-3 py-1 text-xs font-medium text-on-accent hover:brightness-110 disabled:opacity-50">
              {saving ? 'Saving…' : 'Save & re-index'}
            </button>
            <button type="button" onClick={cancel} disabled={saving}
              className="rounded border border-border-default px-3 py-1 text-xs text-cream-secondary hover:bg-ink-hover disabled:opacity-50">
              Cancel
            </button>
          </div>
        </div>
      ) : (
        /* <pre> preserves code-block whitespace in the original material. */
        <pre className="whitespace-pre-wrap break-words font-sans text-sm text-cream-secondary">
          {chunk.content}
        </pre>
      )}
    </li>
  );
}
