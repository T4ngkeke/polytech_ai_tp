/**
 * DocumentManager.jsx — [v7.1] Teacher ingestion visibility (Phase 6).
 *
 * The document author is the ground-truth oracle for "was this processed well?",
 * so this surface lets a teacher:
 *   - upload a PDF with its type (Course=CM / Exercises=TD/TP) + audience,
 *   - see each document's ingestion status + summary (pages / chunks / exercises),
 *   - open a document to read the indexed chunks (the inspector) + exercises,
 *   - [v7.2] edit a chunk (re-embeds + re-indexes) or an exercise to fix
 *     chunking / extraction mistakes.
 *
 * Props:
 *   labId: string — the lab whose documents are shown (required)
 *
 * Backed by:
 *   GET  /api/teacher/labs/{labId}/documents
 *   POST /api/teacher/labs/{labId}/documents     (multipart: file, doc_type, audience)
 *   GET  /api/teacher/documents/{id}/chunks
 *   GET  /api/teacher/documents/{id}/exercises
 *   PUT  /api/teacher/documents/{id}/chunks/{chunkId}        (edit + re-index)
 *   PUT  /api/teacher/documents/{id}/exercises/{exerciseId}  (edit)
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../lib/api';
import { ingestReportWarnings } from '../lib/ingestReport';

// [v7.2] TD and TP are behaviourally identical (both extract exercises), so the
// teacher only picks between course slides and exercises. The combined option is
// stored as `TD` on the backend; existing `TP` docs still display as "TD/TP".
const DOC_TYPE_OPTIONS = [
  { value: 'CM', label: 'Course (CM)' },
  { value: 'TD', label: 'Exercises (TD/TP)' },
  { value: 'corrigé', label: 'Answers (corrigé)' },
];
const AUDIENCES = ['student', 'teacher'];
// [v8.0 §12] Document language selects the BM25 tsvector config; ingest and query
// sides must match. fr is the backend default.
const LANGUAGES = [{ value: 'fr', label: 'Français' }, { value: 'en', label: 'English' }];

const docTypeLabel = (t) => (t === 'CM' ? 'CM' : 'TD/TP');

// Status → badge classes. Coloured chips read fine on the dark panel.
const STATUS_BADGE = {
  pending: 'bg-ink-surface text-cream-muted',
  processing: 'bg-cyan-muted text-cyan',
  indexed: 'bg-emerald-500/15 text-emerald-300',
  failed: 'bg-danger-muted text-danger',
  needs_review: 'bg-gold-muted text-gold',
};

const IN_PROGRESS = new Set(['pending', 'processing']);

// [v8.0] Hint review lifecycle: none → generating → pending_review → approved/failed.
const HINT_STATUS_BADGE = {
  none: 'bg-ink-surface text-cream-muted',
  generating: 'bg-cyan-muted text-cyan animate-pulse',
  pending_review: 'bg-gold-muted text-gold',
  approved: 'bg-emerald-500/15 text-emerald-300',
  failed: 'bg-danger-muted text-danger',
};
const HINT_STATUS_LABEL = {
  none: 'no hints', generating: 'generating…', pending_review: 'review',
  approved: 'approved', failed: 'failed',
};

export default function DocumentManager({ labId }) {
  const [documents, setDocuments] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(null); // document summary being inspected
  const [uploading, setUploading] = useState(false);

  const fileRef = useRef(null);
  const [docType, setDocType] = useState('CM');
  const [audience, setAudience] = useState('student');
  const [shared, setShared] = useState(false);            // CM class-wide
  const [answersFor, setAnswersFor] = useState('');        // corrigé → target TD
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
    form.append('language', language);
    if (docType === 'CM' && shared) form.append('shared', 'true');
    if (docType === 'corrigé' && answersFor) form.append('answers_for_document_id', answersFor);

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
      `Delete "${doc.filename}" and everything extracted from it (exercises, answers, chunks)? This cannot be undone.`
    )) return;
    try {
      await api.delete(`/api/teacher/documents/${doc.id}`);
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
    return (
      <DocumentDetail
        document={selected}
        onBack={() => setSelected(null)}
      />
    );
  }

  return (
    <div className="space-y-4">
      <form onSubmit={handleUpload} className="flex flex-wrap items-end gap-3 rounded-lg border border-border-default bg-ink-deep/30 p-3">
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-cream-muted">PDF file</label>
          <input ref={fileRef} type="file" accept="application/pdf,.pdf" className="text-sm text-cream-secondary file:mr-2 file:rounded file:border-0 file:bg-ink-surface file:px-2 file:py-1 file:text-cream-secondary" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-cream-muted">Type</label>
          <select value={docType} onChange={(e) => setDocType(e.target.value)} className="rounded border border-border-default bg-ink-deep px-2 py-1 text-sm text-cream focus:border-cyan/40 focus:outline-none">
            {DOC_TYPE_OPTIONS.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-cream-muted">Audience</label>
          <select value={audience} onChange={(e) => setAudience(e.target.value)} className="rounded border border-border-default bg-ink-deep px-2 py-1 text-sm text-cream focus:border-cyan/40 focus:outline-none">
            {AUDIENCES.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-cream-muted">Language</label>
          <select value={language} onChange={(e) => setLanguage(e.target.value)} className="rounded border border-border-default bg-ink-deep px-2 py-1 text-sm text-cream focus:border-cyan/40 focus:outline-none">
            {LANGUAGES.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
          </select>
        </div>
        {docType === 'CM' && (
          <label className="flex items-center gap-1.5 pb-1.5 text-xs text-cream-muted">
            <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)}
              className="accent-cyan" />
            Share class-wide
          </label>
        )}
        {docType === 'corrigé' && (
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-cream-muted">Answers for</label>
            <select value={answersFor} onChange={(e) => setAnswersFor(e.target.value)}
              className="rounded border border-border-default bg-ink-deep px-2 py-1 text-sm text-cream focus:border-cyan/40 focus:outline-none">
              <option value="">whole lab (by number)</option>
              {documents.filter((d) => d.doc_type === 'TD' || d.doc_type === 'TP').map((d) => (
                <option key={d.id} value={d.id}>{d.filename}</option>
              ))}
            </select>
          </div>
        )}
        <button
          type="submit"
          disabled={uploading}
          className="rounded gradient-cyan px-3 py-1.5 text-sm font-medium text-cream hover:brightness-110 disabled:opacity-50"
        >
          {uploading ? 'Uploading…' : 'Upload'}
        </button>
      </form>

      {loading ? (
        <p className="text-sm text-cream-muted">Loading documents…</p>
      ) : documents.length === 0 ? (
        <p className="text-sm text-cream-muted">No documents yet. Upload a PDF above.</p>
      ) : (
        <ul className="divide-y divide-border-subtle rounded-lg border border-border-default">
          {documents.map((doc) => (
            <li key={doc.id} className="flex items-stretch hover:bg-ink-hover">
              <button
                type="button"
                onClick={() => setSelected(doc)}
                className="flex min-w-0 flex-1 items-center justify-between gap-3 px-3 py-2 text-left"
              >
                <span className="flex min-w-0 items-center gap-2">
                  <span className="truncate text-sm font-medium text-cream">{doc.filename}</span>
                  {doc.doc_type && (
                    <span className="rounded bg-cyan-muted px-1.5 py-0.5 text-xs text-cyan border border-cyan/20">{docTypeLabel(doc.doc_type)}</span>
                  )}
                  {(doc.status === 'needs_review' || doc.status === 'failed') && (
                    <span title={doc.error_message || ''} className="text-gold">⚠</span>
                  )}
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  <StatusBadge status={doc.status} />
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
      {status.replace('_', ' ')}
    </span>
  );
}

function SummaryChips({ doc }) {
  return (
    <span className="hidden gap-1 text-xs text-cream-muted sm:flex">
      <span title="pages">📄 {doc.page_count ?? '–'}</span>
      <span title="chunks">🧩 {doc.chunk_count}</span>
      <span title="exercises">✎ {doc.exercise_count}</span>
    </span>
  );
}

function IngestReport({ report }) {
  const warnings = ingestReportWarnings(report);
  if (warnings.length === 0) return null;
  return (
    <ul className="mt-2 space-y-1 rounded bg-gold-muted p-2 text-xs text-gold">
      {warnings.map((w, i) => <li key={i}>⚠ {w}</li>)}
    </ul>
  );
}

function DocumentDetail({ document: doc, onBack }) {
  const [chunks, setChunks] = useState([]);
  const [exercises, setExercises] = useState([]);
  const [loading, setLoading] = useState(true);

  const [genBusy, setGenBusy] = useState(false);

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

  // [v8.0] While any exercise is generating, poll exercises so the badges settle
  // to pending_review/failed once the worker finishes.
  const anyGenerating = exercises.some((e) => e.hint_status === 'generating');
  useEffect(() => {
    if (!anyGenerating) return;
    const id = setInterval(async () => {
      try { setExercises(await api.get(`/api/teacher/documents/${doc.id}/exercises`)); } catch {}
    }, 4000);
    return () => clearInterval(id);
  }, [anyGenerating, doc.id]);

  const generateAll = async (urgent) => {
    if (urgent && !window.confirm(
      'Process now? Generation will compete with students for compute. Continue?')) return;
    setGenBusy(true);
    try {
      const { queued } = await api.post(
        `/api/teacher/documents/${doc.id}/generate-hints`, { urgent });
      if (queued === 0) toast('All exercises already have hints (nothing queued).');
      else toast.success(`Queued hint generation for ${queued} exercise${queued > 1 ? 's' : ''}`);
      setExercises(await api.get(`/api/teacher/documents/${doc.id}/exercises`));
    } catch (err) {
      toast.error(err.message);
    } finally {
      setGenBusy(false);
    }
  };

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
          {docTypeLabel(doc.doc_type)} · {doc.audience} · {doc.page_count ?? '–'} pages ·
          {' '}{doc.chunk_count} chunks · {doc.exercise_count} exercises
        </p>
        {doc.error_message && (
          <p className="mt-2 rounded bg-gold-muted p-2 text-xs text-gold">{doc.error_message}</p>
        )}
        <IngestReport report={doc.ingest_report} />
      </div>

      {loading ? (
        <p className="text-sm text-cream-muted">Loading processing report…</p>
      ) : (
        <>
          {exercises.length > 0 && (
            <section>
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <h4 className="text-sm font-semibold text-cream-secondary">Extracted exercises</h4>
                <div className="flex items-center gap-2">
                  {anyGenerating && <span className="text-xs text-cyan">generating…</span>}
                  <button type="button" onClick={() => generateAll(false)} disabled={genBusy}
                    className="rounded gradient-cyan px-2.5 py-1 text-xs font-medium text-cream hover:brightness-110 disabled:opacity-50">
                    {genBusy ? 'Queueing…' : 'Generate hints'}
                  </button>
                  <button type="button" onClick={() => generateAll(true)} disabled={genBusy}
                    title="Skip the queue and process now"
                    className="rounded border border-border-default px-2.5 py-1 text-xs text-cream-secondary hover:bg-ink-hover disabled:opacity-50">
                    Now
                  </button>
                </div>
              </div>
              <p className="mb-2 text-xs text-cream-muted">
                Batch only fills exercises with no hints; students see a hint only after you approve it.
              </p>
              <ul className="space-y-2">
                {exercises.map((ex) => (
                  <EditableExercise
                    key={ex.id}
                    docId={doc.id}
                    exercise={ex}
                    onSaved={(updated) => setExercises((prev) =>
                      prev.map((e) => (e.id === updated.id ? updated : e)))}
                  />
                ))}
              </ul>
            </section>
          )}

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
        </>
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
              className="rounded gradient-cyan px-3 py-1 text-xs font-medium text-cream hover:brightness-110 disabled:opacity-50">
              {saving ? 'Saving…' : 'Save & re-index'}
            </button>
            <button type="button" onClick={cancel} disabled={saving}
              className="rounded border border-border-default px-3 py-1 text-xs text-cream-secondary hover:bg-ink-hover disabled:opacity-50">
              Cancel
            </button>
          </div>
        </div>
      ) : (
        /* <pre> preserves code-block whitespace from TP documents. */
        <pre className="whitespace-pre-wrap break-words font-sans text-sm text-cream-secondary">
          {chunk.content}
        </pre>
      )}
    </li>
  );
}

function EditableExercise({ docId, exercise, onSaved }) {
  const [editing, setEditing] = useState(false);
  const [number, setNumber] = useState(exercise.number);
  const [statement, setStatement] = useState(exercise.statement);
  // [v8.0] hints is a tiered array; edit one tier per line.
  const [hintText, setHintText] = useState((exercise.hints || []).join('\n'));
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState(false); // generate/approve in flight

  const status = exercise.hint_status || 'none';
  const isBlind = exercise.hint_source === 'blind';

  const save = async () => {
    if (!number.trim() || !statement.trim()) { toast.error('Number and statement are required.'); return; }
    setSaving(true);
    try {
      // A hand-edited tiered array; the backend marks it approved (trusted).
      const tiers = hintText.split('\n').map((s) => s.trim()).filter(Boolean);
      const updated = await api.put(`/api/teacher/documents/${docId}/exercises/${exercise.id}`, {
        number, statement, hints: tiers.length ? tiers : null,
      });
      onSaved(updated);
      setEditing(false);
      toast.success('Exercise updated');
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSaving(false);
    }
  };

  const cancel = () => {
    setNumber(exercise.number);
    setStatement(exercise.statement);
    setHintText((exercise.hints || []).join('\n'));
    setEditing(false);
  };

  const regenerate = async () => {
    setBusy(true);
    try {
      await api.post(`/api/teacher/documents/${docId}/exercises/${exercise.id}/generate-hints`, {});
      onSaved({ ...exercise, hint_status: 'generating' });
      toast.success('Queued — generating hints');
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    setBusy(true);
    try {
      const updated = await api.put(
        `/api/teacher/documents/${docId}/exercises/${exercise.id}/hints/approve`, {});
      onSaved(updated);
      toast.success('Approved — students can now see these hints');
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };

  if (editing) {
    return (
      <li className="space-y-2 rounded border border-border-default p-2">
        <input
          value={number}
          onChange={(e) => setNumber(e.target.value)}
          placeholder="Number (e.g. Exercice 1)"
          className="w-full rounded border border-border-default bg-ink-deep px-2 py-1 text-sm text-cream focus:border-cyan/40 focus:outline-none"
        />
        <textarea
          value={statement}
          onChange={(e) => setStatement(e.target.value)}
          rows={Math.min(12, Math.max(2, statement.split('\n').length + 1))}
          placeholder="Statement"
          className="w-full resize-y rounded border border-border-default bg-ink-deep p-2 text-sm text-cream focus:border-cyan/40 focus:outline-none"
        />
        <label className="block text-xs text-cream-muted">Hints — one tier per line (L1 → L3). Saving marks them approved.</label>
        <textarea
          value={hintText}
          onChange={(e) => setHintText(e.target.value)}
          rows={Math.min(8, Math.max(3, hintText.split('\n').length + 1))}
          placeholder={'A gentle nudge\nThe method\nClose, but stop short of the answer'}
          className="w-full resize-y rounded border border-border-default bg-ink-deep p-2 text-sm text-cream focus:border-cyan/40 focus:outline-none"
        />
        <div className="flex gap-2">
          <button type="button" onClick={save} disabled={saving}
            className="rounded gradient-cyan px-3 py-1 text-xs font-medium text-cream hover:brightness-110 disabled:opacity-50">
            {saving ? 'Saving…' : 'Save'}
          </button>
          <button type="button" onClick={cancel} disabled={saving}
            className="rounded border border-border-default px-3 py-1 text-xs text-cream-secondary hover:bg-ink-hover disabled:opacity-50">
            Cancel
          </button>
        </div>
      </li>
    );
  }

  const tiers = exercise.hints || [];
  return (
    <li className="rounded border border-border-default p-2">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <p className="truncate text-sm font-medium text-cream">{exercise.number}</p>
          <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${HINT_STATUS_BADGE[status]}`}>
            {HINT_STATUS_LABEL[status]}
          </span>
          {isBlind && (
            <span title="Solved without an answer key — audit before approving"
              className="rounded bg-danger-muted px-1.5 py-0.5 text-xs font-medium text-danger">⚠ blind</span>
          )}
        </div>
        <button type="button" onClick={() => setEditing(true)} className="shrink-0 text-xs text-cyan hover:underline">
          Edit
        </button>
      </div>
      <p className="mt-1 whitespace-pre-wrap text-sm text-cream-secondary">{exercise.statement}</p>

      {tiers.length > 0 && (
        <ol className="mt-2 space-y-1 border-l-2 border-border-subtle pl-3">
          {tiers.map((t, i) => (
            <li key={i} className="text-xs text-cream-muted">
              <span className="mr-1 font-semibold text-cyan">L{i + 1}</span>{t}
            </li>
          ))}
        </ol>
      )}
      {exercise.hint_reason && status === 'failed' && (
        <p className="mt-1 text-xs text-danger">Reason: {exercise.hint_reason}</p>
      )}

      <div className="mt-2 flex flex-wrap gap-2">
        {status === 'pending_review' && (
          <button type="button" onClick={approve} disabled={busy}
            className="rounded bg-emerald-500/20 px-2.5 py-1 text-xs font-medium text-emerald-300 hover:bg-emerald-500/30 disabled:opacity-50">
            Approve
          </button>
        )}
        {status !== 'generating' && (
          <button type="button" onClick={regenerate} disabled={busy}
            className="rounded border border-border-default px-2.5 py-1 text-xs text-cream-secondary hover:bg-ink-hover disabled:opacity-50">
            {status === 'none' ? 'Generate hints' : 'Regenerate'}
          </button>
        )}
      </div>
    </li>
  );
}
