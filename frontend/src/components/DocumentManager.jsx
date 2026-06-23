/**
 * DocumentManager.jsx — [v7.1] Teacher ingestion visibility (Phase 6).
 *
 * The document author is the ground-truth oracle for "was this processed well?",
 * so this surface lets a teacher:
 *   - upload a PDF with its doc_type (CM/TD/TP) + audience (student/teacher),
 *   - see each document's ingestion status + summary (pages / chunks / exercises),
 *   - open a document to read the actual indexed chunks (the inspector) + exercises.
 *
 * Props:
 *   labId: string — the lab whose documents are shown (required)
 *
 * Backed by:
 *   GET  /api/teacher/labs/{labId}/documents
 *   POST /api/teacher/labs/{labId}/documents     (multipart: file, doc_type, audience)
 *   GET  /api/teacher/documents/{id}/chunks
 *   GET  /api/teacher/documents/{id}/exercises
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../lib/api';

const DOC_TYPES = ['CM', 'TD', 'TP'];
const AUDIENCES = ['student', 'teacher'];

// Status → Tailwind badge classes. Derived inline; no effect/state needed.
const STATUS_BADGE = {
  pending: 'bg-gray-100 text-gray-700',
  processing: 'bg-blue-100 text-blue-700',
  indexed: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
  needs_review: 'bg-amber-100 text-amber-800',
};

const IN_PROGRESS = new Set(['pending', 'processing']);

export default function DocumentManager({ labId }) {
  const [documents, setDocuments] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(null); // document summary being inspected
  const [uploading, setUploading] = useState(false);

  const fileRef = useRef(null);
  const [docType, setDocType] = useState('CM');
  const [audience, setAudience] = useState('student');

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
    if (!documents.some((d) => IN_PROGRESS.has(d.status))) return;
    const id = setInterval(() => {
      loadDocuments().catch(() => {});
    }, 3000);
    return () => clearInterval(id);
  }, [documents, loadDocuments]);

  const handleUpload = async (e) => {
    e.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file) {
      toast.error('Choose a PDF first.');
      return;
    }
    const form = new FormData();
    form.append('file', file);
    form.append('doc_type', docType);
    form.append('audience', audience);

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

  if (!labId) {
    return <p className="text-sm text-gray-500">Select a lab to manage its documents.</p>;
  }

  if (selected) {
    return (
      <DocumentDetail
        document={selected}
        onBack={() => setSelected(null)}
      />
    );
  }

  return (
    <div className="space-y-4">
      <form onSubmit={handleUpload} className="flex flex-wrap items-end gap-3 rounded-lg border border-gray-200 p-3">
        <div className="flex flex-col">
          <label className="text-xs font-medium text-gray-600">PDF file</label>
          <input ref={fileRef} type="file" accept="application/pdf,.pdf" className="text-sm" />
        </div>
        <div className="flex flex-col">
          <label className="text-xs font-medium text-gray-600">Type</label>
          <select value={docType} onChange={(e) => setDocType(e.target.value)} className="rounded border border-gray-300 px-2 py-1 text-sm">
            {DOC_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <div className="flex flex-col">
          <label className="text-xs font-medium text-gray-600">Audience</label>
          <select value={audience} onChange={(e) => setAudience(e.target.value)} className="rounded border border-gray-300 px-2 py-1 text-sm">
            {AUDIENCES.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
        </div>
        <button
          type="submit"
          disabled={uploading}
          className="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {uploading ? 'Uploading…' : 'Upload'}
        </button>
      </form>

      {loading ? (
        <p className="text-sm text-gray-500">Loading documents…</p>
      ) : documents.length === 0 ? (
        <p className="text-sm text-gray-500">No documents yet. Upload a PDF above.</p>
      ) : (
        <ul className="divide-y divide-gray-100 rounded-lg border border-gray-200">
          {documents.map((doc) => (
            <li key={doc.id}>
              <button
                type="button"
                onClick={() => setSelected(doc)}
                className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left hover:bg-gray-50"
              >
                <span className="flex min-w-0 items-center gap-2">
                  <span className="truncate text-sm font-medium text-gray-800">{doc.filename}</span>
                  {doc.doc_type && (
                    <span className="rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-600">{doc.doc_type}</span>
                  )}
                  {(doc.status === 'needs_review' || doc.status === 'failed') && (
                    <span title={doc.error_message || ''} className="text-amber-600">⚠</span>
                  )}
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  <StatusBadge status={doc.status} />
                  <SummaryChips doc={doc} />
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function StatusBadge({ status }) {
  const cls = STATUS_BADGE[status] || 'bg-gray-100 text-gray-700';
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium ${cls}`}>
      {status.replace('_', ' ')}
    </span>
  );
}

function SummaryChips({ doc }) {
  return (
    <span className="hidden gap-1 text-xs text-gray-500 sm:flex">
      <span title="pages">📄 {doc.page_count ?? '–'}</span>
      <span title="chunks">🧩 {doc.chunk_count}</span>
      <span title="exercises">✎ {doc.exercise_count}</span>
    </span>
  );
}

function DocumentDetail({ document: doc, onBack }) {
  const [chunks, setChunks] = useState([]);
  const [exercises, setExercises] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    // Fetch chunks + exercises in parallel — no waterfall.
    Promise.all([
      api.get(`/api/teacher/documents/${doc.id}/chunks`),
      api.get(`/api/teacher/documents/${doc.id}/exercises`),
    ])
      .then(([c, e]) => {
        if (!alive) return;
        setChunks(c);
        setExercises(e);
      })
      .catch((err) => toast.error(err.message))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [doc.id]);

  return (
    <div className="space-y-4">
      <button type="button" onClick={onBack} className="text-sm text-blue-600 hover:underline">
        ← Back to documents
      </button>

      <div className="rounded-lg border border-gray-200 p-3">
        <div className="flex items-center gap-2">
          <h3 className="truncate font-medium text-gray-800">{doc.filename}</h3>
          <StatusBadge status={doc.status} />
        </div>
        <p className="mt-1 text-xs text-gray-500">
          {doc.doc_type} · {doc.audience} · {doc.page_count ?? '–'} pages ·
          {' '}{doc.chunk_count} chunks · {doc.exercise_count} exercises
        </p>
        {doc.error_message && (
          <p className="mt-2 rounded bg-amber-50 p-2 text-xs text-amber-800">{doc.error_message}</p>
        )}
      </div>

      {loading ? (
        <p className="text-sm text-gray-500">Loading processing report…</p>
      ) : (
        <>
          {exercises.length > 0 && (
            <section>
              <h4 className="mb-2 text-sm font-semibold text-gray-700">Extracted exercises</h4>
              <ul className="space-y-2">
                {exercises.map((ex, i) => (
                  <li key={i} className="rounded border border-gray-200 p-2">
                    <p className="text-sm font-medium text-gray-800">{ex.number}</p>
                    <p className="whitespace-pre-wrap text-sm text-gray-700">{ex.statement}</p>
                    {ex.hints && <p className="mt-1 text-xs text-gray-500">Hint: {ex.hints}</p>}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section>
            <h4 className="mb-2 text-sm font-semibold text-gray-700">
              Chunk inspector ({chunks.length})
            </h4>
            {chunks.length === 0 ? (
              <p className="text-sm text-gray-500">No chunks indexed for this document.</p>
            ) : (
              <ul className="space-y-2">
                {chunks.map((c) => (
                  <li key={c.chunk_index} className="rounded border border-gray-200 p-2">
                    <p className="mb-1 text-xs text-gray-400">
                      #{c.chunk_index}
                      {c.page_no != null && ` · p.${c.page_no}`}
                      {c.section && ` · ${c.section}`}
                    </p>
                    {c.context && (
                      <p className="mb-1 rounded bg-blue-50 p-1.5 text-xs italic text-blue-700">
                        context: {c.context}
                      </p>
                    )}
                    {/* <pre> preserves code-block whitespace from TP documents. */}
                    <pre className="whitespace-pre-wrap break-words font-sans text-sm text-gray-700">
                      {c.content}
                    </pre>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  );
}
