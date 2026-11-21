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
import {
  boxStyle, cropStyle, displayPieces, pictureNumbers, visibleIssues, withoutText,
} from '../lib/documentBlocks';
import { useT } from '../lib/i18n';
import Icon from './Icon';

const KINDS = ['heading', 'text', 'code', 'formula', 'table', 'figure', 'navigation'];
const DESCRIBABLE = new Set(['figure', 'formula', 'table']);
const STATES = new Set(['queued', 'processing', 'parsed', 'ready', 'partial', 'failed']);
const PUBLICATIONS = new Set(['draft', 'published', 'superseded']);
const REASONS = new Set(['upload', 'reparse', 'teacher_edit']);

// Machine values of the server shown in the language of the page (unknown ones as they are).
function useLabels() {
  const t = useT();
  return {
    state: (v) => (STATES.has(v) ? t(`p.block.state.${v}`) : v),
    publication: (v) => (PUBLICATIONS.has(v) ? t(`p.block.pub.${v}`) : v),
    reason: (v) => (REASONS.has(v) ? t(`p.block.reason.${v}`) : v),
    kind: (v) => (KINDS.includes(v) ? t(`p.block.kind.${v}`) : v),
    // How search treats the block (same cases as lib/documentBlocks.js searchNote).
    searchNote: (block) => {
      if (block.index_state === 'excluded') return t('p.block.note.excluded');
      if (withoutText(block)) return t('p.block.note.noText');
      if (block.index_state === 'needs_review') return t('p.block.note.notSearched');
      if (block.type === 'figure' && block.edited_by_teacher) return t('p.block.note.described');
      return null;
    },
  };
}

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
  const t = useT();
  const label = useLabels();
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
  const change = async (action, fn) => {
    setBusy(true);
    try {
      const out = await fn({ expected_revision_id: revisionId, expected_generation: versions.generation });
      const newId = out?.revision_id || out?.revision?.revision_id;
      toast.success(newId ? t('p.block.draftCreated', { action }) : t('p.block.actionSaved', { action }));
      await loadVersions(newId || revisionId);
      if (!newId) await loadPage();
      onRefresh?.().catch(() => {});
      return true;
    } catch (err) {
      toast.error(err.status === 409 ? t('p.block.conflict', { message: err.message }) : err.message);
      if (err.status === 409) await loadVersions(revisionId).catch(() => {});
      return false;
    } finally {
      setBusy(false);
    }
  };

  const reprocess = () => change(t('p.block.act.reprocess'), (target) =>
    api.post(`/api/teacher/documents/${doc.id}/revisions/${revisionId}/reprocess`,
      { expected_generation: target.expected_generation }));

  // Only a version with pages that were not read waits for the teacher;
  // every other version is published as soon as it is indexed.
  const publish = async () => {
    setBusy(true);
    try {
      await api.post(`${base}/publish`, {
        expected_generation: versions.generation,
        acknowledge_issues: revision.processing_state === 'partial',
      });
      toast.success(t('p.block.published'));
      await loadVersions(revisionId);
      onRefresh?.().catch(() => {});
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };
  const canPublish = revision && revision.publication_state === 'draft' && revision.index_ready
    && ['ready', 'partial'].includes(revision.processing_state);

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
      <button type="button" onClick={onBack} className="cursor-pointer rounded-full px-3 py-1 text-[14px] text-cream-secondary hover:bg-ink-hover">← {t('p.block.back')}</button>
      <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-border-subtle bg-ink-raised p-4">
        <h3 className="min-w-0 truncate text-[15px] font-semibold text-cream">{doc.filename}</h3>
        {versions && (
          <select value={revisionId || ''} onChange={(e) => setRevisionId(e.target.value)}
            aria-label={t('p.block.version')}
            className="rounded-xl border border-border-default bg-ink-raised px-2 py-1.5 text-[13px] text-cream">
            {versions.revisions.map((r, i) => (
              <option key={r.id} value={r.id}>
                v{i + 1} · {label.reason(r.reason)} · {label.state(r.processing_state)} · {label.publication(r.publication_state)}
                {r.id === versions.current_revision_id ? ` (${t('p.block.pub.published')})` : ''}
              </option>
            ))}
          </select>
        )}
        {canPublish && (
          <button type="button" disabled={busy} onClick={publish}
            title={revision.processing_state === 'partial' ? t('p.block.publishPartialTitle') : undefined}
            className="cursor-pointer rounded-full bg-ink px-4 py-1.5 text-[13px] text-on-ink hover:opacity-90 disabled:opacity-50">
            {revision.processing_state === 'partial' ? t('p.block.publishAnyway') : t('p.block.publish')}
          </button>
        )}
        <button type="button" disabled={busy || !revision} onClick={reprocess}
          className="cursor-pointer rounded-full border border-border-default px-3 py-1.5 text-[13px] text-cream-secondary hover:bg-ink-hover disabled:opacity-50">
          {t('p.block.reprocess')}
        </button>
        <button type="button" disabled={!revision} onClick={downloadSource}
          className="cursor-pointer rounded-full border border-border-default px-3 py-1.5 text-[13px] text-cream-secondary hover:bg-ink-hover disabled:opacity-50">
          {t('p.block.download')}
        </button>
      </div>
      {revision && <RevisionSummary revision={revision} />}
      {revision && !hasContent && (
        <p className="text-[14px] text-cream-muted">
          {revision.processing_state === 'failed'
            ? t('p.block.versionFailed', { code: revision.report?.error_code || t('p.block.unknownError') })
            : t('p.block.stillProcessing')}
        </p>
      )}
      {hasContent && (
        <div className="grid gap-4 md:grid-cols-[14rem_1fr]">
          <nav className="max-h-[70vh] space-y-1 overflow-y-auto">
            {outline.map((p) => (
              <button key={p.page_id} type="button" onClick={() => setPageId(p.page_id)}
                className={`block w-full cursor-pointer rounded-lg px-2 py-1.5 text-left text-[13px] ${p.page_id === pageId ? 'bg-ink-hover text-cream' : 'text-cream-secondary hover:bg-ink-hover'}`}>
                <span className="font-medium">{t('p.block.page', { n: p.source_slide_no })}</span>
                {p.state !== 'usable' && <span className="ml-1 text-gold"><Icon name="warning" className="inline-block w-3.5 h-3.5 align-[-2px]" /></span>}
                <span className="block truncate text-cream-muted"><DisplayText text={p.title || p.summary || '—'} /></span>
              </button>
            ))}
            {outlineCursor && (
              <button type="button" onClick={moreOutline} className="cursor-pointer rounded-full px-2 py-1 text-[13px] text-cream-secondary hover:bg-ink-hover">{t('p.block.morePages')}</button>
            )}
          </nav>
          {page && <PageView page={page} base={base} busy={busy} change={change} docId={doc.id} />}
        </div>
      )}
    </div>
  );
}

/** Text read from a PDF, with its sub- and superscripts shown as such (see displayPieces). */
function DisplayText({ text, kind }) {
  return displayPieces(text, kind).map((piece, i) => (
    piece.script === 'sub' ? <sub key={i}>{piece.text}</sub>
      : piece.script === 'sup' ? <sup key={i}>{piece.text}</sup>
        : <span key={i}>{piece.text}</span>));
}

function RevisionSummary({ revision }) {
  const t = useT();
  const label = useLabels();
  const report = revision.report || {};
  const notes = [];
  const unread = report.index?.unread_pages || [];
  if (report.totals?.pages != null) notes.push(t('p.block.nPages', { n: report.totals.pages }));
  if (unread.length) notes.push(t('p.block.notRead', { pages: unread.join(', ') }));
  if (report.carried_forward) {
    notes.push(t('p.block.carried', { a: report.carried_forward.carried, b: report.carried_forward.reviews }));
  }
  return (
    <p className="text-[13px] text-cream-muted">
      {label.state(revision.processing_state)} · {label.publication(revision.publication_state)}
      {notes.length > 0 && ` · ${notes.join(' · ')}`}
      {revision.processing_state === 'parsed' && ` · ${revision.reason === 'teacher_edit'
        ? t('p.block.goesLive')
        : t('p.block.notBeforeIndex')}`}
    </p>
  );
}

function PageView({ page, base, busy, change, docId }) {
  const t = useT();
  const label = useLabels();
  const preview = useAssetUrl(page.page.preview_asset_id ? `${base}/assets/${page.page.preview_asset_id}` : null);
  const [ratio, setRatio] = useState(null); // page width / height, from the preview
  const [active, setActive] = useState(null); // block under the pointer
  const numbers = pictureNumbers(page.blocks);
  const unsearched = page.blocks.filter(withoutText).length;
  const pageRatio = ratio || (page.page.width_cm && page.page.height_cm ? page.page.width_cm / page.page.height_cm : null);
  // Reviews tied to a block are shown under it; the others above the blocks.
  const loose = (page.reviews || []).filter((r) => !page.blocks.some((b) => b.block_id === r.block_id));
  const show = (blockId) => window.document.getElementById(`block-${blockId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2 text-[14px] text-cream">
        <span className="font-semibold">{t('p.block.page', { n: page.page.source_slide_no })}</span>
        {page.page.preview_mapping !== 'verified' && <span className="text-[13px] text-cream-muted">{t('p.block.previewUnverified')}</span>}
      </div>
      {preview ? (
        <div className="relative inline-block max-w-full overflow-hidden rounded">
          <img src={preview} alt={t('p.block.previewAlt', { n: page.page.source_slide_no })}
            onLoad={(e) => setRatio(e.currentTarget.naturalWidth / e.currentTarget.naturalHeight)}
            className="block max-h-[28rem] max-w-full rounded border border-border-default" />
          {page.blocks.filter((b) => b.region && (numbers[b.block_id] || b.block_id === active)).map((b) => (
            <button key={b.block_id} type="button" onClick={() => show(b.block_id)}
              onMouseEnter={() => setActive(b.block_id)} onMouseLeave={() => setActive(null)}
              aria-label={numbers[b.block_id] ? t('p.block.picture', { n: numbers[b.block_id] }) : label.kind(b.type)}
              style={boxStyle(b.region)}
              className={`absolute cursor-pointer rounded-sm border-2 ${b.block_id === active
                ? 'z-10 border-[3px] border-yellow-400 bg-yellow-300/30 shadow-[0_0_0_9999px_rgba(0,0,0,0.25)]'
                : 'border-yellow-400/70 hover:border-yellow-400'}`}>
              {numbers[b.block_id] && (
                <span className="absolute -left-0.5 -top-0.5 rounded-br bg-yellow-400 px-1 text-[10px] font-semibold leading-4 text-neutral-900">
                  {numbers[b.block_id]}
                </span>
              )}
            </button>
          ))}
        </div>
      ) : <p className="rounded-xl bg-ink-surface p-2 text-[13px] text-cream-muted">{t('p.block.noPreview')}</p>}
      {page.unavailable.filter((u) => u.scope === 'page').length > 0 && (
        <ul className="space-y-1 rounded-xl bg-warn-soft p-2 text-[13px] text-gold">
          {page.unavailable.filter((u) => u.scope === 'page').map((u, i) => (
            <li key={i}><Icon name="warning" className="inline-block w-3.5 h-3.5 align-[-2px]" /> {u.message}</li>
          ))}
        </ul>
      )}
      {unsearched > 0 && (
        <p className="text-[13px] text-cream-muted">
          {unsearched === 1 ? t('p.block.unsearched1') : t('p.block.unsearchedN', { n: unsearched })}
        </p>
      )}
      {loose.map((r) => <ReviewCard key={r.id} review={r} busy={busy} change={change} base={base} />)}
      <ul className="space-y-2">
        {page.blocks.map((b) => (
          <BlockCard key={b.block_id} block={b} number={numbers[b.block_id]} busy={busy} change={change} docId={docId}
            preview={preview} pageRatio={pageRatio} active={b.block_id === active} onActive={setActive}
            reviews={(page.reviews || []).filter((r) => r.block_id === b.block_id)} base={base} />
        ))}
      </ul>
    </section>
  );
}

function ReviewCard({ review, busy, change, base }) {
  const t = useT();
  const [text, setText] = useState(review.candidate_content || '');
  const resolve = (status) => change(status === 'confirmed' ? t('p.block.act.confirm') : t('p.block.act.ignore'), (target) =>
    api.put(`${base}/reviews/${review.id}`, {
      expected_generation: target.expected_generation, review_revision: review.revision, status,
      ...(status === 'confirmed' ? { content: text } : {}),
    }));
  return (
    <div className="mt-2 rounded-xl border border-gold/40 p-2 text-[13px]">
      <p className="text-gold">
        {review.kind === 'vision_description' ? t('p.block.modelDesc', { model: review.model }) : review.reason}
      </p>
      {review.uncertainties?.length > 0 && <p className="text-cream-muted">{t('p.block.notSure', { items: review.uncertainties.join('; ') })}</p>}
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3}
        className="mt-1 w-full rounded-lg border border-border-default bg-ink-raised p-2 font-mono text-cream" />
      <div className="mt-1 flex gap-2">
        <button type="button" disabled={busy || !text.trim()} onClick={() => resolve('confirmed')} className="cursor-pointer rounded-full bg-ink px-3 py-1 text-on-ink hover:opacity-90 disabled:opacity-50">{t('p.block.useIt')}</button>
        <button type="button" disabled={busy} onClick={() => resolve('ignored')} className="cursor-pointer rounded-full px-3 py-1 text-cream-secondary hover:bg-ink-hover disabled:opacity-50">{t('p.block.ignore')}</button>
      </div>
    </div>
  );
}

// The region of the page preview where the block is.
function Crop({ preview, region, pageRatio, label }) {
  if (!preview || !region || !pageRatio) return null;
  const crop = cropStyle(region, pageRatio);
  return (
    <div style={crop.frame} className="relative max-h-40 w-40 shrink-0 overflow-hidden rounded border border-border-default bg-white">
      <img src={preview} alt={label} style={crop.image} className="absolute max-w-none" />
    </div>
  );
}

function BlockCard({ block, number, busy, change, docId, preview, pageRatio, active, onActive, reviews, base }) {
  const t = useT();
  const label = useLabels();
  const [editing, setEditing] = useState(false);
  const [content, setContent] = useState(block.content);
  const [kind, setKind] = useState(block.type);
  const [description, setDescription] = useState(block.content_description || '');
  const url = `/api/teacher/documents/${docId}/blocks/${block.block_id}`;
  const excluded = block.index_state === 'excluded';
  const bare = withoutText(block);
  const note = label.searchNote(block);
  const issues = visibleIssues(block);
  const name = number ? t('p.block.picture', { n: number }) : label.kind(block.type);

  const save = async () => {
    const body = {};
    if (content !== block.content) body.content = content;
    if (kind !== block.type) body.kind = kind;
    if (description !== (block.content_description || '')) {
      if (description.trim()) body.content_description = description; else body.clear_description = true;
    }
    if (Object.keys(body).length === 0) { setEditing(false); return; }
    // The typed text is kept if the save fails.
    if (await change(t('edit'), (target) => api.put(url, { ...target, ...body }))) setEditing(false);
  };

  return (
    <li id={`block-${block.block_id}`} onMouseEnter={() => onActive(block.block_id)} onMouseLeave={() => onActive(null)}
      className={`rounded-xl border p-2.5 ${active ? 'border-yellow-400' : 'border-border-subtle'} ${excluded ? 'border-dashed opacity-70' : ''}`}>
      <div className="mb-1 flex flex-wrap items-center gap-2 text-[13px] text-cream-muted">
        {number && <span className="rounded bg-yellow-400 px-1 text-[10px] font-semibold leading-4 text-neutral-900">{number}</span>}
        <span className="font-medium text-cream-secondary">{name}</span>
        {note && <span className="rounded bg-ink-surface px-1.5">{note}</span>}
        {block.edited_by_teacher && <span className="text-cream-secondary">{t('p.block.teacherRevised')}</span>}
        <span className="ml-auto flex flex-wrap gap-x-3 gap-y-1">
          {!editing && (
            <button type="button" disabled={busy} onClick={() => setEditing(true)} className="cursor-pointer text-cream-secondary underline hover:text-cream">
              {bare ? t('p.block.addDesc') : t('edit')}
            </button>
          )}
          {DESCRIBABLE.has(block.type) && (
            <button type="button" disabled={busy}
              onClick={() => change(t('p.block.act.describe'), (target) => api.post(`${url}/describe`, target))}
              title={t('p.block.aiTitle')}
              className="cursor-pointer text-cream-secondary underline hover:text-cream">{t('p.block.aiWrite')}</button>
          )}
          <button type="button" disabled={busy}
            onClick={() => change(excluded ? t('p.block.act.include') : t('p.block.act.exclude'), (target) => api.post(`${url}/exclude`, { ...target, excluded: !excluded }))}
            title={excluded ? t('p.block.includeTitle') : t('p.block.excludeTitle')}
            className="cursor-pointer text-cream-secondary underline hover:text-cream">{excluded ? t('p.block.include') : t('p.block.exclude')}</button>
        </span>
      </div>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
        {block.type === 'figure' && <Crop preview={preview} region={block.region} pageRatio={pageRatio} label={name} />}
        <div className="min-w-0 flex-1">
          {editing ? (
            <div className="space-y-2">
              <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label={t('p.block.kindLabel')} className="rounded-xl border border-border-default bg-ink-raised px-2 py-1.5 text-[13px] text-cream">
                {KINDS.map((k) => <option key={k} value={k}>{label.kind(k)}</option>)}
              </select>
              <textarea value={content} onChange={(e) => setContent(e.target.value)}
                rows={Math.min(16, Math.max(3, content.split('\n').length + 1))}
                placeholder={bare ? t('p.block.barePlaceholder') : undefined}
                aria-label={t('p.block.contentLabel')}
                className="w-full resize-y rounded-xl border border-border-default bg-ink-raised p-2 font-mono text-[14px] text-cream" />
              <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={400}
                placeholder={t('p.block.descPlaceholder')}
                className="w-full rounded-xl border border-border-default bg-ink-raised px-2 py-1.5 text-[13px] text-cream" />
              <div className="flex gap-2">
                <button type="button" onClick={save} disabled={busy} className="cursor-pointer rounded-full bg-ink px-4 py-1.5 text-[13px] text-on-ink hover:opacity-90 disabled:opacity-50">{t('save')}</button>
                <button type="button" disabled={busy} onClick={() => { setEditing(false); setContent(block.content); setKind(block.type); setDescription(block.content_description || ''); }}
                  className="cursor-pointer rounded-full px-3 py-1.5 text-[13px] text-cream-secondary hover:bg-ink-hover">{t('cancel')}</button>
              </div>
            </div>
          ) : (
            block.content
              ? <pre className="whitespace-pre-wrap break-words font-mono text-sm text-cream-secondary"><DisplayText text={block.content} kind={block.type} /></pre>
              : !bare && <p className="text-[14px] text-cream-muted">—</p>
          )}
          {block.content_description && !editing && <p className="mt-1 text-[13px] italic text-cream-secondary"><DisplayText text={block.content_description} /></p>}
          {block.edited_by_teacher && block.source_text != null && block.source_text !== block.content && (
            <details className="mt-1 text-[13px] text-cream-muted"><summary>{t('p.block.sourceText')}</summary>
              <pre className="whitespace-pre-wrap font-mono">{block.source_text}</pre></details>
          )}
          {issues.length > 0 && (
            <ul className="mt-1 text-[13px] text-gold">{issues.map((i, n) => <li key={n}><Icon name="warning" className="inline-block w-3.5 h-3.5 align-[-2px]" /> {i.message}</li>)}</ul>
          )}
          {reviews.map((r) => <ReviewCard key={r.id} review={r} busy={busy} change={change} base={base} />)}
          <details className="mt-1 text-[12px] text-cream-muted">
            <summary className="cursor-pointer">{t('p.block.details')}</summary>
            <p>{t('p.block.detailsRead')}: {block.source_completeness} · {t('p.block.detailsSearch')}: {block.index_state} · {block.source.extraction_method}
              {block.description_origin !== 'none' ? ` · ${t('p.block.detailsDesc')}: ${block.description_origin}` : ''}</p>
            {(block.issues || []).filter((i) => !issues.includes(i)).map((i, n) => <p key={n}>{i.code}: {i.message}</p>)}
          </details>
        </div>
      </div>
    </li>
  );
}
