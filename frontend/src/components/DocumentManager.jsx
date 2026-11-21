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
import { displayStatus } from '../lib/documentProgress';
import { useT } from '../lib/i18n';
import StructuredDocumentDetail from './DocumentBlockEditor';
import Icon from './Icon';

// Status → badge classes. Coloured chips read fine on the dark panel.
const STATUS_BADGE = {
  pending: 'bg-ink-surface text-cream-muted',
  processing: 'bg-ink-hover text-cream-secondary',
  indexed: 'bg-success-muted text-success',
  partially_indexed: 'bg-gold-muted text-gold',
  failed: 'bg-danger-muted text-danger',
  needs_review: 'bg-gold-muted text-gold',
  parsed: 'bg-ink-hover text-cream-secondary',
};

// Shown on the warning icon when the server gave no message.
const WARNING_HINT = {
  needs_review: 'p.docs.hint.needs_review',
  partially_indexed: 'p.docs.hint.partially_indexed',
  failed: 'p.docs.hint.failed',
};

const STATUS_KEYS = new Set(['pending', 'processing', 'indexed', 'parsed', 'needs_review', 'partially_indexed', 'failed']);
const IN_PROGRESS = new Set(['pending', 'processing', 'parsed']);

// Upload refusals the server names by a code (routers/teacher.py).
const UPLOAD_ERROR = {
  only_pdf: 'p.docs.err.only_pdf',
  odp_not_supported: 'p.docs.err.odp_not_supported',
  file_too_large: 'p.docs.err.file_too_large',
  structured_ingest_disabled: 'p.docs.err.structured_ingest_disabled',
};
const isPdf = (file) => file.name.toLowerCase().endsWith('.pdf');
const fileSize = (bytes) => (bytes < 1048576 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1048576).toFixed(1)} MB`);
const fileKey = (file) => `${file.name}|${file.size}|${file.lastModified}`;
// A row still to send: newly added, or failed and sent again on the next Upload.
const toSend = (row) => row.state === 'chosen' || row.state === 'failed';

// The pages read so far and the time left, for a document still on its way
// (same numbers as lib/documentProgress.js readingLine, in the shown language).
function readingLineT(status, latest, t) {
  if (status === 'pending') {
    const ahead = latest?.jobs_ahead ?? 0;
    return { text: ahead ? t('p.docs.waitingAhead', { n: ahead }) : t('p.docs.waiting'), share: 0 };
  }
  if (status === 'processing') {
    const p = latest?.progress;
    if (!p?.total) return { text: t('p.docs.opening'), share: 0 };
    let left = '';
    if (p.seconds_left != null) {
      left = p.seconds_left < 60 ? t('p.docs.lessMinute') : t('p.docs.minutesLeft', { n: Math.round(p.seconds_left / 60) });
    }
    return {
      text: `${t('p.docs.pagesRead', { done: p.done, total: p.total })}${left ? ` · ${left}` : ''}`,
      share: 0.95 * Math.min(1, p.done / p.total),
    };
  }
  if (status === 'parsed') return { text: t('p.docs.indexing'), share: 0.97 };
  return null;
}

export default function DocumentManager({ labId, classId, examMode = false, onOpenClassSettings }) {
  const t = useT();
  const base = classId ? `/api/teacher/classes/${classId}/documents` : labId ? `/api/teacher/labs/${labId}/documents` : null;
  const [documents, setDocuments] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(null); // document summary being inspected
  const [uploading, setUploading] = useState(false);
  // The upload list: { key, file, state: chosen|sending|done|failed, sent, error }.
  const [files, setFiles] = useState([]);
  const fileRef = useRef(null);
  const [confirming, setConfirming] = useState(null); // document whose deletion waits for Confirm

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

  // Add the picked files to the list; the same file twice is kept once.
  const addFiles = (picked) => {
    const skipped = picked.filter((file) => !isPdf(file)).map((file) => file.name);
    if (skipped.length) toast.error(t('p.docs.notPdf', { files: skipped.join(', ') }), { duration: 6000 });
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
      toast.error(t('p.docs.addFirst'));
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
        setRow(key, { state: 'failed', error: UPLOAD_ERROR[err.message] ? t(UPLOAD_ERROR[err.message]) : err.message });
        failed += 1;
      }
    }
    setUploading(false);
    // The sent files are in the document list now; only the failed ones stay here, to retry.
    setFiles((rows) => rows.filter((row) => row.state !== 'done'));
    if (failed) toast.error(t('p.docs.someFailed', { done, failed }), { duration: 8000 });
    else toast.success(done === 1 ? t('p.docs.uploaded1') : t('p.docs.uploadedN', { n: done }), { duration: 6000 });
  };

  const deleteDocument = async (doc) => {
    setConfirming(null);
    try {
      // A structured document is deleted with the generation the teacher saw.
      await api.delete(`/api/teacher/documents/${doc.id}${doc.structured ? `?expected_generation=${doc.generation}` : ''}`);
      toast.success(t('p.docs.deleted'));
      if (selected?.id === doc.id) setSelected(null);
      await loadDocuments();
    } catch (err) {
      toast.error(err.message);
    }
  };

  if (!base) {
    return <p className="text-[14px] text-cream-muted">{t('p.docs.selectLab')}</p>;
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
        <div role="status" className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-gold/40 bg-warn-soft px-3 py-2 text-[13px] text-cream">
          <Icon name="lock" className="h-4 w-4 shrink-0 text-gold" />
          <p className="min-w-0 flex-1">
            <span className="font-semibold text-gold">{t('p.docs.examOn')}</span>{' '}
            {t('p.docs.examText')}
          </p>
          {onOpenClassSettings && (
            <button type="button" onClick={onOpenClassSettings}
              className="shrink-0 cursor-pointer rounded-full border border-gold/40 px-3 py-1 text-[13px] text-cream hover:bg-ink-hover">
              {t('p.docs.classSettings')}
            </button>
          )}
        </div>
      )}
      <p className="text-[13px] text-cream-muted">
        {classId ? t('p.docs.introClass') : t('p.docs.introLab')}
      </p>
      <form onSubmit={handleUpload} className="space-y-3 rounded-2xl border border-border-subtle bg-ink-raised p-5">
        <div>
          <h3 className="text-[15px] font-semibold text-cream">{classId ? t('p.docs.classMaterial') : t('p.docs.labMaterial')}</h3>
          <p className="mt-0.5 text-[13px] text-cream-muted">{t('p.docs.addHint')}</p>
        </div>
        {files.length > 0 && <UploadList rows={files} busy={uploading} onRemove={removeFile} />}
        <div className="flex flex-wrap items-center gap-3">
          <label className={`shrink-0 ${uploading ? 'pointer-events-none opacity-50' : 'cursor-pointer'}`}>
            <input ref={fileRef} type="file" className="sr-only" multiple disabled={uploading}
              accept="application/pdf,.pdf"
              onChange={(e) => addFiles(Array.from(e.target.files || []))} />
            <span className="inline-block rounded-full border border-border-default px-4 py-2 text-[14px] text-cream-secondary hover:bg-ink-hover">
              {files.some(toSend) ? t('p.docs.addMore') : t('p.docs.addPdfs')}
            </span>
          </label>
          <button
            type="submit"
            disabled={uploading || !files.some(toSend)}
            className="cursor-pointer rounded-full bg-ink px-4 py-2 text-[14px] text-on-ink hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {uploading ? t('p.docs.uploading')
              : files.filter(toSend).length > 1 ? t('p.docs.uploadN', { n: files.filter(toSend).length }) : t('p.docs.upload')}
          </button>
          {!uploading && files.length > 0 && (
            <button type="button" onClick={() => setFiles([])}
              className="cursor-pointer rounded-full px-3 py-1.5 text-[13px] text-cream-muted hover:bg-ink-hover">
              {t('p.docs.clear')}
            </button>
          )}
        </div>
      </form>

      {documents.some((d) => IN_PROGRESS.has(displayStatus(d))) && (
        <p className="text-[13px] text-cream-muted">{t('p.docs.slowNote')}</p>
      )}
      {loading ? (
        <p className="text-[14px] text-cream-muted">{t('p.docs.loading')}</p>
      ) : documents.length === 0 ? (
        <p className="text-[14px] text-cream-muted">{t('p.docs.none')}</p>
      ) : (
        <ul className="divide-y divide-border-subtle overflow-hidden rounded-2xl border border-border-subtle bg-ink-raised">
          {documents.map((doc) => { const status = displayStatus(doc); return (
            <li key={doc.id} className="flex flex-wrap items-stretch">
              <div className={`flex min-w-0 flex-1 items-stretch ${opens(doc) ? 'hover:bg-ink-hover' : ''}`}>
              <button
                type="button"
                onClick={() => setSelected(doc)}
                disabled={!opens(doc)}
                title={readOnly(doc) ? t('p.docs.sharedTitle')
                  : !doc.structured ? t('p.docs.legacyTitle') : undefined}
                className="flex min-w-0 flex-1 flex-col gap-1.5 px-3 py-2 text-left disabled:cursor-default"
              >
                <span className="flex w-full min-w-0 flex-col items-start gap-1 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
                <span className="flex min-w-0 max-w-full items-center gap-2">
                  {!classId && <ScopeBadge scope={doc.scope} />}
                  <span className="truncate text-[14px] font-medium text-cream">{doc.filename}</span>
                  {WARNING_HINT[status] && (
                    <span title={doc.error_message || t(WARNING_HINT[status])} className="text-gold"><Icon name="warning" className="w-4 h-4" /></span>
                  )}
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  <StatusBadge status={status} />
                  <SummaryChips doc={doc} />
                </span>
                </span>
                <ReadingLine line={readingLineT(status, doc.latest_revision, t)} />
              </button>
              {readOnly(doc) ? <span className="w-[42px] shrink-0" /> : (
                <button
                  type="button"
                  onClick={() => setConfirming(doc)}
                  title={t('p.docs.deleteDoc')}
                  aria-label={t('p.docs.deleteNamed', { name: doc.filename })}
                  className="shrink-0 cursor-pointer px-3 text-cream-muted hover:text-danger"
                >
                  <Icon name="trash" className="w-4 h-4" />
                </button>
              )}
              </div>
              {confirming?.id === doc.id && (
                <div role="alertdialog" aria-label={t('p.docs.deleteDoc')}
                  className="flex w-full flex-wrap items-center gap-x-3 gap-y-2 border-t border-border-subtle bg-danger-deep px-3 py-2">
                  <p className="min-w-0 flex-1 text-[13px] text-cream">{t('p.docs.deleteAsk', { name: doc.filename })}</p>
                  <button type="button" onClick={() => deleteDocument(doc)}
                    className="cursor-pointer rounded-full bg-danger px-4 py-1.5 text-[13px] text-white hover:bg-danger-hover">{t('p.confirm')}</button>
                  <button type="button" onClick={() => setConfirming(null)}
                    className="cursor-pointer rounded-full px-3 py-1.5 text-[13px] text-cream-secondary hover:bg-ink-hover">{t('cancel')}</button>
                </div>
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
  const t = useT();
  return (
    <ul className="divide-y divide-border-subtle rounded-xl border border-border-subtle bg-ink-base text-[14px]">
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
            {row.state === 'sending' && t('p.docs.sending', { n: Math.round(100 * row.sent) })}
            {row.state === 'done' && t('p.docs.sentReading')}
            {row.state === 'failed' && row.error}
          </span>
          {toSend(row) && !busy ? (
            <button type="button" onClick={() => onRemove(row.key)} title={t('p.docs.removeFromList')}
              aria-label={t('p.docs.removeNamed', { name: row.file.name })} className="shrink-0 cursor-pointer text-cream-muted hover:text-danger">
              <Icon name="close" className="h-4 w-4" />
            </button>
          ) : <span className="w-4 shrink-0" />}
        </li>
      ))}
    </ul>
  );
}

function ScopeBadge({ scope }) {
  const t = useT();
  return scope === 'class'
    ? <span className="shrink-0 rounded-full bg-warn-soft px-2 py-0.5 text-[12px] text-gold">{t('scope.class')}</span>
    : <span className="shrink-0 rounded-full bg-ink-hover px-2 py-0.5 text-[12px] text-cream-secondary">{t('p.docs.scopeLab')}</span>;
}

// The pages read so far and the time left, under a document being processed.
function ReadingLine({ line }) {
  const t = useT();
  if (!line) return null;
  return (
    <span className="flex w-full items-center gap-3">
      <span className="h-1.5 w-20 shrink-0 overflow-hidden rounded bg-ink-surface sm:w-48" role="progressbar"
        aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(100 * line.share)} aria-label={t('p.docs.processing')}>
        <span className={`block h-full rounded bg-cream-secondary transition-[width] duration-700 ${line.share ? '' : 'w-1/4 animate-pulse'}`}
          style={line.share ? { width: `${100 * line.share}%` } : undefined} />
      </span>
      <span className="min-w-0 text-[12px] leading-snug text-cream-muted">{line.text}</span>
    </span>
  );
}

function StatusBadge({ status }) {
  const t = useT();
  const cls = STATUS_BADGE[status] || 'bg-ink-surface text-cream-muted';
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium ${cls}`}>
      {STATUS_KEYS.has(status) ? t(`p.docs.status.${status}`) : status.replace('_', ' ')}
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
  const t = useT();
  const pages = currentPages(doc.ingest_report);
  return (
    <span className="hidden gap-1 text-xs text-cream-muted sm:flex">
      <span title={t('p.docs.pages')} className="inline-flex items-center gap-1"><Icon name="file" className="w-3.5 h-3.5" />{doc.page_count ?? '–'}</span>
      <span title={t('p.docs.chunks')} className="inline-flex items-center gap-1"><Icon name="layers" className="w-3.5 h-3.5" />{doc.chunk_count}</span>
      {pages && <span>{t('p.docs.usable', { a: pages.usable, b: pages.unavailable })}{pages.noIndex ? ` · ${t('p.docs.notIndexed', { n: pages.noIndex })}` : ''}</span>}
      {pages?.pending != null && <span>{t('p.docs.reviewsPending', { n: pages.pending })}</span>}
    </span>
  );
}

