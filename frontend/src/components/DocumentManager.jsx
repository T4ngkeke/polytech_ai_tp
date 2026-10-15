/**
 * DocumentManager.jsx — the teacher's course documents.
 *
 * A teacher uploads course PDFs, sees each document's processing state and
 * summary (pages / chunks), and opens a document to review and edit what
 * MinerU read (DocumentBlockEditor). Documents from before the PDF path
 * (Markdown, HTML, older uploads) are listed and can be deleted only.
 *
 * The upload form used to ask for a type (CM / TD-TP / corrigé) and an audience.
 * Both are gone: everything a teacher uploads is course material the whole class
 * can read. So are the exercise editor, the hint review lifecycle and the
 * segmentation compare-and-confirm panel — there are no exercise rows to review.
 *
 * Props (one of them):
 *   classId: string — the class-wide documents of a class, shared by all its labs
 *   labId: string — the documents a lab uses: the class-wide ones (read-only
 *     here, marked Class) and the lab's own (marked Lab)
 *
 * Backed by:
 *   GET    /api/teacher/classes/{classId}/documents
 *   POST   /api/teacher/classes/{classId}/documents (multipart: file)
 *   GET    /api/teacher/labs/{labId}/documents
 *   POST   /api/teacher/labs/{labId}/documents     (multipart: file)
 *   DELETE /api/teacher/documents/{id}
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../lib/api';
import StructuredDocumentDetail from './DocumentBlockEditor';
import Icon from './Icon';

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

const STATUS_LABEL = {
  parsed: 'parsed · waiting for indexing',
  needs_review: 'needs review',
  partially_indexed: 'some pages not read',
};

// Shown on the warning icon when the server gave no message.
const WARNING_HINT = {
  needs_review: 'Some pages could not be read. Open the document to check them and publish.',
  partially_indexed: 'Some pages could not be read: students cannot find their content.',
  failed: 'Processing failed. Open the document to reprocess it.',
};

const IN_PROGRESS = new Set(['pending', 'processing']);

// A structured document has no published version until indexing
// exists (M3). Until then its newest draft tells what is going on.
const REVISION_STATUS = { queued: 'pending', processing: 'processing', parsed: 'parsed', partial: 'needs_review', failed: 'failed' };
function displayStatus(doc) {
  if (doc.structured && !doc.current_revision_id && doc.latest_revision) {
    return REVISION_STATUS[doc.latest_revision.processing_state] || doc.latest_revision.processing_state;
  }
  return doc.status;
}

export default function DocumentManager({ labId, classId }) {
  const base = classId ? `/api/teacher/classes/${classId}/documents` : labId ? `/api/teacher/labs/${labId}/documents` : null;
  const [documents, setDocuments] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(null); // document summary being inspected
  const [uploading, setUploading] = useState(false);

  const fileRef = useRef(null);
  const [fileName, setFileName] = useState('');

  const loadDocuments = useCallback(async () => {
    if (!base) return [];
    const rows = await api.get(base);
    setDocuments(rows);
    return rows;
  }, [base]);

  // Initial load when the class or lab changes.
  useEffect(() => {
    if (!base) return;
    setSelected(null);
    setLoading(true);
    loadDocuments()
      .catch((e) => toast.error(e.message))
      .finally(() => setLoading(false));
  }, [base, loadDocuments]);

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

    setUploading(true);
    try {
      await api.upload(base, form);
      if (fileRef.current) fileRef.current.value = '';
      setFileName('');
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

  if (!base) {
    return <p className="text-sm text-cream-muted">Select a lab to manage its documents.</p>;
  }
  // In a lab, the class-wide documents are shown but managed at the class level.
  const readOnly = (doc) => !classId && doc.scope === 'class';
  // Documents from before the PDF path have no reader view; they can be deleted.
  const opens = (doc) => doc.structured && !readOnly(doc);

  if (selected) {
    return (
      <StructuredDocumentDetail
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
      <p className="text-xs text-cream-muted">
        {classId
          ? 'Documents uploaded here are shared by every lab of this class, e.g. the course handout.'
          : "Upload this lab's own material here. Documents marked Class come from the class Documents tab, shared by every lab; manage them there."}
      </p>
      <form onSubmit={handleUpload} className="flex flex-wrap items-end gap-3 rounded-lg border border-border-default bg-ink-deep/30 p-3">
        <div className="flex min-w-0 flex-col gap-1">
          <span className="text-xs font-medium text-cream-muted">{classId ? 'Class material' : 'Lab material'}</span>
          <label className="flex min-w-0 cursor-pointer items-center gap-2">
            <input ref={fileRef} type="file" className="sr-only"
              accept="application/pdf,.pdf"
              onChange={(e) => setFileName(e.target.files?.[0]?.name || '')} />
            <span className="shrink-0 rounded border border-border-default bg-ink-surface px-3 py-1 text-sm text-cream-secondary hover:border-cyan/40 hover:text-cream">
              Choose file
            </span>
            <span className="truncate text-sm text-cream-muted">{fileName || 'PDF'}</span>
          </label>
        </div>
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
          {documents.map((doc) => { const status = displayStatus(doc); return (
            <li key={doc.id} className={`flex items-stretch ${opens(doc) ? 'hover:bg-ink-hover' : ''}`}>
              <button
                type="button"
                onClick={() => setSelected(doc)}
                disabled={!opens(doc)}
                title={readOnly(doc) ? 'Shared by the class: open the class Documents tab to manage it'
                  : !doc.structured ? 'Uploaded before the PDF path: it can only be deleted' : undefined}
                className="flex min-w-0 flex-1 flex-col items-start gap-1 px-3 py-2 text-left disabled:cursor-default sm:flex-row sm:items-center sm:justify-between sm:gap-3"
              >
                <span className="flex min-w-0 max-w-full items-center gap-2">
                  {!classId && <ScopeBadge scope={doc.scope} />}
                  <span className="truncate text-sm font-medium text-cream">{doc.filename}</span>
                  {WARNING_HINT[status] && (
                    <span title={doc.error_message || WARNING_HINT[status]} className="text-gold"><Icon name="warning" className="w-4 h-4" /></span>
                  )}
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  <StatusBadge status={status} />
                  <SummaryChips doc={doc} />
                </span>
              </button>
              {readOnly(doc) ? <span className="w-[42px] shrink-0" /> : (
                <button
                  type="button"
                  onClick={(e) => deleteDocument(doc, e)}
                  title="Delete document"
                  aria-label={`Delete ${doc.filename}`}
                  className="shrink-0 px-3 text-cream-muted hover:text-danger"
                >
                  <Icon name="trash" className="w-4 h-4" />
                </button>
              )}
            </li>
          ); })}
        </ul>
      )}
    </div>
  );
}

function ScopeBadge({ scope }) {
  return scope === 'class'
    ? <span className="shrink-0 rounded bg-gold-muted px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-gold">Class</span>
    : <span className="shrink-0 rounded bg-cyan-muted px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-cyan">Lab</span>;
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
      <span title="pages" className="inline-flex items-center gap-1"><Icon name="file" className="w-3.5 h-3.5" />{doc.page_count ?? '–'}</span>
      <span title="chunks" className="inline-flex items-center gap-1"><Icon name="layers" className="w-3.5 h-3.5" />{doc.chunk_count}</span>
      {pages && <span>{pages.usable} usable · {pages.unavailable} unavailable{pages.noIndex ? ` · ${pages.noIndex} not indexed` : ''}</span>}
      {pages?.pending != null && <span>{pages.pending} visual reviews pending</span>}
    </span>
  );
}
