/**
 * DocumentManager.jsx — the teacher's course documents.
 *
 * A teacher uploads course PDFs: files are added to a list (several at once or
 * one pick after another, each removable), then sent one after the other, each
 * with its own result. The teacher sees each document's processing state and
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
import { displayStatus, readingLine } from '../lib/documentProgress';
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
  parsed: 'indexing',
  needs_review: 'needs review',
  partially_indexed: 'some pages not read',
};

// Shown on the warning icon when the server gave no message.
const WARNING_HINT = {
  needs_review: 'Some pages could not be read. Open the document to check them and publish.',
  partially_indexed: 'Some pages could not be read: students cannot find their content.',
  failed: 'Processing failed. Open the document to reprocess it.',
};

const IN_PROGRESS = new Set(['pending', 'processing', 'parsed']);

// Upload refusals the server names by a code (routers/teacher.py).
const UPLOAD_ERROR = {
  only_pdf: 'Only PDF files can be uploaded.',
  odp_not_supported: 'Export the slides to PDF first.',
  file_too_large: 'The file is too large.',
  structured_ingest_disabled: 'Uploads are turned off on this server.',
};
const isPdf = (file) => file.name.toLowerCase().endsWith('.pdf');
const fileSize = (bytes) => (bytes < 1048576 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1048576).toFixed(1)} MB`);
const fileKey = (file) => `${file.name}|${file.size}|${file.lastModified}`;
// A row still to send: newly added, or failed and sent again on the next Upload.
const toSend = (row) => row.state === 'chosen' || row.state === 'failed';

export default function DocumentManager({ labId, classId, examMode = false, onOpenClassSettings }) {
  const base = classId ? `/api/teacher/classes/${classId}/documents` : labId ? `/api/teacher/labs/${labId}/documents` : null;
  const [documents, setDocuments] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(null); // document summary being inspected
  const [uploading, setUploading] = useState(false);
  // The upload list: { key, file, state: chosen|sending|done|failed, sent, error }.
  const [files, setFiles] = useState([]);
  const fileRef = useRef(null);

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
    setFiles([]);
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

  // Add the picked files to the list. Files already sent leave it; the same file twice is kept once.
  const addFiles = (picked) => {
    const skipped = picked.filter((file) => !isPdf(file)).map((file) => file.name);
    if (skipped.length) toast.error(`Not a PDF, not added: ${skipped.join(', ')}`, { duration: 6000 });
    setFiles((rows) => {
      const kept = rows.filter((row) => row.state !== 'done');
      const known = new Set(kept.map((row) => row.key));
      const added = picked.filter((file) => isPdf(file) && !known.has(fileKey(file)))
        .map((file) => ({ key: fileKey(file), file, state: 'chosen', sent: 0 }));
      return [...kept, ...added];
    });
    if (fileRef.current) fileRef.current.value = '';   // the same file can be picked again after a removal
  };
  const removeFile = (key) => setFiles((rows) => rows.filter((row) => row.key !== key));

  const handleUpload = async (e) => {
    e.preventDefault();
    const queue = files.filter(toSend);
    if (!queue.length) {
      toast.error('Add a PDF first.');
      return;
    }
    const setRow = (key, patch) => setFiles((rows) => rows.map((row) => (row.key === key ? { ...row, ...patch } : row)));
    setUploading(true);
    let done = 0;
    let failed = 0;
    // One file at a time: the server reads them in the order they arrive anyway.
    for (const { key, file } of queue) {
      setRow(key, { state: 'sending', sent: 0, error: null });
      const form = new FormData();
      form.append('file', file);
      try {
        await api.uploadWithProgress(base, form, (sent) => setRow(key, { sent }));
        setRow(key, { state: 'done', sent: 1 });
        done += 1;
        await loadDocuments().catch(() => {});
      } catch (err) {
        setRow(key, { state: 'failed', error: UPLOAD_ERROR[err.message] || err.message });
        failed += 1;
      }
    }
    setUploading(false);
    if (failed) toast.error(`${done} uploaded, ${failed} failed. Upload again to retry the failed ones.`, { duration: 8000 });
    else toast.success(`${done === 1 ? 'Uploaded' : `${done} files uploaded`}. The pages are being read; you can leave this page meanwhile.`, { duration: 6000 });
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
      {examMode && (
        // Exam mode keeps only the allowed files open; uploads go on, for later.
        <div role="status" className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-gold/40 bg-gold-muted px-3 py-2 text-xs text-cream">
          <Icon name="lock" className="h-4 w-4 shrink-0 text-gold" />
          <p className="min-w-0 flex-1">
            <span className="font-semibold text-gold">Exam mode is on.</span>{' '}
            Students can search and open only the files you allow in the exam (Class settings); the
            others wait until exam mode is off. You can still upload and publish files.
          </p>
          {onOpenClassSettings && (
            <button type="button" onClick={onOpenClassSettings}
              className="shrink-0 cursor-pointer rounded-lg border border-gold/40 px-2.5 py-1 text-xs text-cream hover:bg-gold/10">
              Class settings
            </button>
          )}
        </div>
      )}
      <p className="text-xs text-cream-muted">
        {classId
          ? 'Documents uploaded here are shared by every lab of this class, e.g. the course handout.'
          : "Upload this lab's own material here. Documents marked Class come from the class Documents tab, shared by every lab; manage them there."}
      </p>
      <form onSubmit={handleUpload} className="space-y-3 rounded-lg border border-border-default bg-ink-deep/30 p-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="text-xs font-medium text-cream-muted">{classId ? 'Class material' : 'Lab material'}</span>
          <span className="text-xs text-cream-muted">Add PDFs, one or several at a time, then upload them together.</span>
        </div>
        {files.length > 0 && <UploadList rows={files} busy={uploading} onRemove={removeFile} />}
        <div className="flex flex-wrap items-center gap-3">
          <label className={`shrink-0 ${uploading ? 'pointer-events-none opacity-50' : 'cursor-pointer'}`}>
            <input ref={fileRef} type="file" className="sr-only" multiple disabled={uploading}
              accept="application/pdf,.pdf"
              onChange={(e) => addFiles(Array.from(e.target.files || []))} />
            <span className="inline-block rounded border border-border-default bg-ink-surface px-3 py-1.5 text-sm text-cream-secondary hover:border-cyan/40 hover:text-cream">
              {files.some(toSend) ? '+ Add more' : '+ Add PDFs'}
            </span>
          </label>
          <button
            type="submit"
            disabled={uploading || !files.some(toSend)}
            className="rounded gradient-cyan px-3 py-1.5 text-sm font-medium text-on-accent hover:brightness-110 disabled:opacity-50"
          >
            {uploading ? 'Uploading…'
              : `Upload${files.filter(toSend).length > 1 ? ` ${files.filter(toSend).length} files` : ''}`}
          </button>
          {!uploading && files.length > 0 && (
            <button type="button" onClick={() => setFiles([])}
              className="cursor-pointer text-xs text-cream-muted hover:text-cream">
              Clear list
            </button>
          )}
        </div>
      </form>

      {documents.some((d) => IN_PROGRESS.has(displayStatus(d))) && (
        <p className="text-xs text-cream-muted">
          A PDF takes about 15 seconds per page to read. You can leave this page: reading goes on
          without it. Pictures then get a short caption for search; while this runs, answers to students
          can be a little slower.
        </p>
      )}
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
                className="flex min-w-0 flex-1 flex-col gap-1.5 px-3 py-2 text-left disabled:cursor-default"
              >
                <span className="flex w-full min-w-0 flex-col items-start gap-1 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
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
                </span>
                <ReadingLine line={readingLine(status, doc.latest_revision)} />
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

// The upload list: files waiting to be sent (removable), then how each went.
function UploadList({ rows, busy, onRemove }) {
  return (
    <ul className="divide-y divide-border-subtle rounded border border-border-subtle bg-ink-base/40 text-sm">
      {rows.map((row) => (
        <li key={row.key} className="flex min-w-0 items-center gap-2 px-2.5 py-1.5">
          <span className="w-4 shrink-0">
            {row.state === 'done' && <Icon name="check" className="h-4 w-4 text-success" />}
            {row.state === 'failed' && <Icon name="warning" className="h-4 w-4 text-danger" />}
            {(row.state === 'chosen' || row.state === 'sending') && <Icon name="file" className="h-4 w-4 text-cream-muted" />}
          </span>
          <span className="min-w-0 flex-1 truncate text-cream-secondary" title={row.file.name}>{row.file.name}</span>
          <span className={`shrink-0 text-xs ${row.state === 'failed' ? 'text-danger' : 'text-cream-muted'}`}>
            {row.state === 'chosen' && fileSize(row.file.size)}
            {row.state === 'sending' && `sending ${Math.round(100 * row.sent)}%`}
            {row.state === 'done' && 'sent, being read'}
            {row.state === 'failed' && row.error}
          </span>
          {toSend(row) && !busy ? (
            <button type="button" onClick={() => onRemove(row.key)} title="Remove from the list"
              aria-label={`Remove ${row.file.name}`} className="shrink-0 cursor-pointer text-cream-muted hover:text-danger">
              <Icon name="close" className="h-4 w-4" />
            </button>
          ) : <span className="w-4 shrink-0" />}
        </li>
      ))}
    </ul>
  );
}

function ScopeBadge({ scope }) {
  return scope === 'class'
    ? <span className="shrink-0 rounded bg-gold-muted px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-gold">Class</span>
    : <span className="shrink-0 rounded bg-cyan-muted px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-cyan">Lab</span>;
}

// The pages read so far and the time left, under a document being processed.
function ReadingLine({ line }) {
  if (!line) return null;
  return (
    <span className="flex w-full items-center gap-3">
      <span className="h-1.5 w-20 shrink-0 overflow-hidden rounded bg-ink-surface sm:w-48" role="progressbar"
        aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(100 * line.share)} aria-label="Processing">
        <span className={`block h-full rounded bg-cyan transition-[width] duration-700 ${line.share ? '' : 'w-1/4 animate-pulse'}`}
          style={line.share ? { width: `${100 * line.share}%` } : undefined} />
      </span>
      <span className="min-w-0 text-[11px] leading-snug text-cream-muted">{line.text}</span>
    </span>
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
      <span title="pages" className="inline-flex items-center gap-1"><Icon name="file" className="w-3.5 h-3.5" />{doc.page_count ?? '–'}</span>
      <span title="chunks" className="inline-flex items-center gap-1"><Icon name="layers" className="w-3.5 h-3.5" />{doc.chunk_count}</span>
      {pages && <span>{pages.usable} usable · {pages.unavailable} unavailable{pages.noIndex ? ` · ${pages.noIndex} not indexed` : ''}</span>}
      {pages?.pending != null && <span>{pages.pending} visual reviews pending</span>}
    </span>
  );
}
