/**
 * Turns.jsx — the conversation of the new student chat: each turn is the
 * student's question (right, light bubble; edit and copy; versions ‹ 1/2 ›)
 * and the answer (no bubble): the process line, the task checklist, the
 * draft while sources are checked, the answer with [E#] / [W#] pills, the
 * Python runs, the course and web sources, the "not from the course" labels,
 * the action row, and the suggested follow-ups under the last answer.
 */
import { lazy, memo, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import Icon from '../../components/Icon';
import { useT } from '../../lib/i18n';
import { distinctImages, groupCitations, linkCitations, linkWeb } from '../../lib/citations';
import { processSummary } from '../../lib/turns';
import { runPython } from '../../lib/python';
import { FeedbackButtons, VisualEvidenceCard, sourceOf } from '../../components/ChatMessage';
import { RunOutput } from '../../components/MessageContent';

const loadContent = () => import('../../components/MessageContent');
const MessageContent = lazy(loadContent);
export const preloadContent = loadContent;

/* ── Process line ── */

function stepText(t, s) {
  switch (s.tool) {
    case 'search': return t('step.search', { q: s.query }) + (s.count != null ? ` · ${t('step.found', { n: s.count })}` : '');
    case 'grep': return t('step.grep', { q: s.query });
    case 'read': case 'open': return s.file ? t('step.read', { file: s.file, page: s.page ?? '?' }) : t('step.readFailed');
    case 'view_image': return t('step.image', { file: s.file || '' });
    case 'search_web': return t('step.web', { q: s.query }) + (s.count != null ? ` · ${t('step.webFound', { n: s.count })}` : '');
    case 'fetch_url': { let host = s.url; try { host = new URL(s.url).hostname; } catch { /* keep */ } return t('step.fetch', { url: host }); }
    case 'execute_code': return t('step.code');
    default: return s.tool;
  }
}

const PHASE_KEYS = { thinking: 'phase.thinking', finding: 'phase.finding', reading: 'phase.reading',
  checking_image: 'phase.image', checking_sources: 'phase.checking', searching_web: 'phase.web',
  running_code: 'phase.code' };

function ProcessLine({ answer, live }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const steps = answer.steps || [];
  const phase = (answer.phases || []).at(-1);
  if (live) {
    return (
      <div className="mb-3 space-y-1 text-[13.5px] text-cream-secondary" role="status" aria-live="polite">
        {steps.map((s, i) => (
          <div key={i} className="flex items-center gap-2">
            <Icon name="check" className="h-3.5 w-3.5 text-cream-muted" />
            <span className="truncate">{stepText(t, s)}</span>
          </div>
        ))}
        <div className="flex items-center gap-2">
          <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-cream-faint border-t-cream-secondary" />
          <span>{t(PHASE_KEYS[phase] || 'phase.thinking')}</span>
        </div>
      </div>
    );
  }
  const sum = processSummary(answer);
  const parts = [];
  if (sum.searched) parts.push(sum.pages ? t('sum.searchedPages', { n: sum.pages }) : t('sum.searched'));
  else if (sum.courseUse === 'none') parts.push(t('sum.noSearch'));
  if (sum.web) parts.push(t('sum.web'));
  if (sum.code) parts.push(t('sum.code', { n: sum.code }));
  if (sum.seconds) parts.push(t('sum.seconds', { n: sum.seconds }));
  if (!parts.length) return null;
  return (
    <div className="mb-2">
      <button type="button" onClick={() => setOpen(!open)} disabled={!steps.length}
        className="inline-flex items-center gap-1 text-[13px] text-cream-muted hover:text-cream-secondary disabled:hover:text-cream-muted">
        {parts.join(' · ')}
        {steps.length > 0 && <Icon name={open ? 'chevronDown' : 'chevronRight'} className="h-3.5 w-3.5" />}
      </button>
      {open && (
        <div className="mt-1.5 space-y-1 border-l border-border-default pl-3 text-[13px] text-cream-secondary">
          {steps.map((s, i) => <div key={i} className="truncate">{stepText(t, s)}</div>)}
        </div>
      )}
    </div>
  );
}

function TaskList({ tasks }) {
  const t = useT();
  const [open, setOpen] = useState(true);
  if (!tasks?.length) return null;
  const done = tasks.filter((x) => x.status === 'completed').length;
  return (
    <div className="mb-3 rounded-xl border border-border-subtle px-3 py-2">
      <button type="button" onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 text-[13px] text-cream-secondary">
        <Icon name="tasks" className="h-4 w-4" />
        <span className="flex-1 text-left">{t('tasks.title', { done, n: tasks.length })}</span>
        <Icon name={open ? 'chevronDown' : 'chevronRight'} className="h-3.5 w-3.5" />
      </button>
      {open && (
        <ul className="mt-1.5 space-y-1">
          {tasks.map((task) => (
            <li key={task.id} className={`flex items-start gap-2 text-[13.5px] ${task.status === 'completed' ? 'text-cream-muted line-through' : task.status === 'cancelled' ? 'text-cream-faint line-through' : 'text-cream'}`}>
              <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${task.status === 'completed' ? 'bg-success' : task.status === 'in_progress' ? 'bg-cyan' : 'bg-ink-elevated'}`} />
              {task.content}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** An image a Python run printed, saved as a chat file (fetched with the session token). */
function StoredImage({ fileId }) {
  const [src, setSrc] = useState(null);
  useEffect(() => {
    let url;
    let current = true;
    api.raw(`/api/files/${fileId}/content`).then(async (r) => {
      if (!r.ok || !current) return;
      url = URL.createObjectURL(await r.blob());
      if (current) setSrc(url);
    }).catch(() => {});
    return () => { current = false; if (url) URL.revokeObjectURL(url); };
  }, [fileId]);
  return src ? <img src={src} alt="" className="my-2 max-h-80 rounded bg-white" /> : null;
}

function CodeRuns({ live, stored }) {
  const t = useT();
  const runs = stored?.length ? stored : live || [];
  const [open, setOpen] = useState(!stored?.length);
  if (!runs.length) return null;
  return (
    <div className="my-3">
      <button type="button" onClick={() => setOpen(!open)} className="inline-flex items-center gap-1.5 text-[13px] text-cream-secondary hover:text-cream">
        <Icon name="terminal" className="h-4 w-4" />
        {t('code.runs', { n: runs.length })}
        <Icon name={open ? 'chevronDown' : 'chevronRight'} className="h-3.5 w-3.5" />
      </button>
      {open && runs.map((run) => (
        <div key={run.id} className="mt-2 overflow-hidden rounded-xl bg-[#0f1115]">
          <pre className="overflow-x-auto px-4 py-3 font-mono text-[12.5px] leading-relaxed text-white/85"><code>{run.code}</code></pre>
          {stored?.length ? (
            <div className="border-t border-white/10 bg-[#16181d] px-4 py-3 text-[12.5px]">
              {run.stdout && <pre className="whitespace-pre-wrap font-mono text-white/85">{run.stdout.replace(/\[image \d+ shown to the student under the code\]\n?/g, '')}</pre>}
              {run.images?.map((id) => <StoredImage key={id} fileId={id} />)}
              {run.stderr && <pre className="mt-1 whitespace-pre-wrap font-mono text-[#fca5a5]">{run.stderr}</pre>}
              {run.status === 'timeout' && <p className="font-mono text-[#fca5a5]">{t(run.where === 'server' ? 'code.timeoutServer' : 'code.timeout')}</p>}
              <div className="mt-2 text-[11px] text-white/40">{t(run.where === 'server' ? 'code.footerServer' : 'code.footer')}</div>
            </div>
          ) : <RunOutput run={run} />}
        </div>
      ))}
    </div>
  );
}

function Sources({ answer, sessionId, onOpenSource: openSource }) {
  const t = useT();
  // The answer's id goes with the page, so that the chat can record the opening.
  const onOpenSource = (source) => openSource({ ...source, messageId: answer.id });
  const files = groupCitations(answer.citations || []);
  const images = distinctImages(answer.citations || []);
  const web = answer.web_sources || [];
  if (!files.length && !web.length && !images.length) return null;
  return (
    <div className="mt-4 space-y-3">
      {images.length > 0 && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {images.slice(0, 3).map((c) => (
            <VisualEvidenceCard key={c.asset_id} citation={c} sessionId={sessionId}
              onOpen={() => onOpenSource(sourceOf(answer.citations, c, sessionId))} />
          ))}
        </div>
      )}
      {files.length > 0 && (
        <div>
          <div className="mb-1.5 text-[12.5px] font-medium text-cream-muted">{t('sources.course')}</div>
          <div className="space-y-1">
            {files.map((file) => (
              <div key={file.key} className="flex flex-wrap items-center gap-1.5 text-[13.5px]">
                <Icon name="book" className="h-4 w-4 shrink-0 text-cyan" />
                <span className="max-w-[260px] truncate text-cream" title={file.filename}>{file.filename}</span>
                {file.newerRevision && <span className="text-[12px] text-cream-muted">· {t('sources.older')}</span>}
                {file.pages.map((page) => (page.available && sessionId ? (
                  <button key={page.pageNo} type="button"
                    onClick={() => onOpenSource({ ...sourceOf(answer.citations, answer.citations.find((c) =>
                      c.document_id === file.documentId && (c.revision_id || null) === file.revisionId), sessionId),
                      page: page.pageNo })}
                    className="rounded-md bg-ink-surface px-1.5 text-[12px] leading-5 text-cream-secondary hover:bg-ink-hover hover:text-cream">
                    {t('sources.page', { n: page.pageNo })}
                  </button>
                ) : (
                  <span key={page.pageNo} className="rounded-md border border-dashed border-border-default px-1.5 text-[12px] leading-[18px] text-cream-muted" title={t('sources.unavailable')}>
                    {t('sources.page', { n: page.pageNo })}
                  </span>
                )))}
              </div>
            ))}
          </div>
        </div>
      )}
      {web.length > 0 && (
        <div>
          <div className="mb-1.5 text-[12.5px] font-medium text-cream-muted">{t('sources.web')}</div>
          <div className="space-y-1">
            {web.map((w) => (
              <a key={w.id} href={w.url} target="_blank" rel="noreferrer noopener"
                className="flex items-center gap-1.5 text-[13.5px] text-cream-secondary hover:text-cream">
                <Icon name="globe" className="h-4 w-4 shrink-0 text-cream-muted" />
                <span className="truncate">{w.title || w.url}</span>
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ActionButton({ icon, label, onClick }) {
  return (
    <button type="button" onClick={onClick} aria-label={label} title={label}
      className="rounded-md p-1.5 text-cream-muted hover:bg-ink-hover hover:text-cream">
      <Icon name={icon} className="h-4 w-4" />
    </button>
  );
}

function VersionSwitch({ index, count, onChange }) {
  const t = useT();
  if (count < 2) return null;
  return (
    <span className="inline-flex items-center text-[12.5px] tabular-nums text-cream-muted">
      <button type="button" aria-label={t('version.prev')} disabled={index === 0} onClick={() => onChange(index - 1)}
        className="rounded p-1 hover:text-cream disabled:opacity-30"><Icon name="chevronLeft" className="h-3.5 w-3.5" /></button>
      {index + 1}/{count}
      <button type="button" aria-label={t('version.next')} disabled={index === count - 1} onClick={() => onChange(index + 1)}
        className="rounded p-1 hover:text-cream disabled:opacity-30"><Icon name="chevronRight" className="h-3.5 w-3.5" /></button>
    </span>
  );
}

function copyText(t, text) {
  const done = () => toast.success(t('copied'));
  if (navigator.clipboard?.writeText) navigator.clipboard.writeText(text).then(done).catch(() => {});
  else {
    const area = document.createElement('textarea');
    area.value = text;
    document.body.appendChild(area);
    area.select();
    try { document.execCommand('copy'); done(); } catch { /* nothing */ }
    area.remove();
  }
}

/* ── The answer ── */

export const Answer = memo(function Answer({ answer, live, isLast, sessionId, onOpenSource, onPick, onType,
  onRegenerate, onSearchCourse, onDeep, onFollowUp, onAskAgain, canRunCode, rateable }) {
  const t = useT();
  const citations = answer.citations;
  const onCite = useCallback((ids) => {
    const labels = ids.split(',');
    const c = citations?.find((x) => x.label === labels[0]);
    if (c && c.available && sessionId) onOpenSource({ ...sourceOf(citations, c, sessionId, labels), messageId: answer.id });
  }, [citations, sessionId, onOpenSource, answer.id]);
  const citeInfo = useCallback((ids) => {
    const c = citations?.find((x) => x.label === ids.split(',')[0]);
    return c ? { filename: c.filename, page: c.pdf_page_no ?? c.page_no, text: (c.text || c.snippet || '').slice(0, 120) } : null;
  }, [citations]);
  const webSources = useMemo(() => new Map((answer.web_sources || []).map((w) => [w.id, w])), [answer.web_sources]);
  const content = useMemo(() => {
    let text = answer.content || '';
    if (citations?.length) {
      const usable = citations.filter((c) => c.available && c.label);
      const pageOf = new Map(usable.map((c) => [c.label, `${c.document_id}:${c.revision_id || ''}:${c.pdf_page_no ?? c.page_no}`]));
      text = linkCitations(text, new Set(pageOf.keys()), pageOf, true);
    }
    return linkWeb(text, new Set(webSources.keys()));
  }, [answer.content, citations, webSources]);
  const onRun = useMemo(() => (canRunCode ? (code, onPhase) => runPython(code, { onPhase }) : null), [canRunCode]);
  const hasText = !!answer.content;
  return (
    <div className="group/answer">
      <ProcessLine answer={answer} live={live && !hasText} />
      <TaskList tasks={answer.tasks} />
      {live && !hasText && answer.draft && (
        <div>
          <div className="opacity-70">
            <Suspense fallback={<p className="whitespace-pre-wrap text-[16px] leading-[1.75]">{answer.draft}</p>}>
              <MessageContent content={answer.draft} streaming />
            </Suspense>
          </div>
          <p className="mt-2 text-[12.5px] text-cream-muted">{t('draft.note')}</p>
        </div>
      )}
      <CodeRuns live={answer.live_runs} stored={answer.code_runs} />
      {hasText ? (
        <Suspense fallback={<p className="whitespace-pre-wrap text-[16px] leading-[1.75]">{answer.content}</p>}>
          <MessageContent content={content} onCite={onCite} citeInfo={citeInfo} webSources={webSources} onRun={onRun} />
        </Suspense>
      ) : !live && (
        <p className="text-[14px] italic text-cream-muted">
          {t(answer.stopped_elsewhere ? 'answer.stoppedElsewhere' : 'answer.none')}{' '}
          {onAskAgain && <button type="button" onClick={onAskAgain} className="not-italic text-cream underline">{t('answer.askAgain')}</button>}
        </p>
      )}
      {answer.clarification?.options?.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2" role="group" aria-label={t('clarify.options')}>
          {answer.clarification.options.map((option) => (
            <button key={option} type="button" disabled={!onPick} onClick={() => onPick?.(option)}
              className="rounded-full border border-border-strong px-3.5 py-1.5 text-[14px] text-cream hover:bg-ink-hover disabled:opacity-50">
              {option}
            </button>
          ))}
          {onPick && <button type="button" onClick={onType} className="text-[13px] text-cream-secondary underline">{t('clarify.type')}</button>}
        </div>
      )}
      {hasText && <Sources answer={answer} sessionId={sessionId} onOpenSource={onOpenSource} />}
      {hasText && !live && answer.course_use === 'none' && !answer.clarification && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="rounded-full bg-warn-soft px-2.5 py-0.5 text-[12.5px] text-gold">{t('use.none')}</span>
          {isLast && onSearchCourse && <button type="button" onClick={onSearchCourse} className="text-[13px] text-cream-secondary underline hover:text-cream">{t('use.searchCourse')}</button>}
        </div>
      )}
      {hasText && !live && answer.course_use === 'not_found' && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="rounded-full bg-warn-soft px-2.5 py-0.5 text-[12.5px] text-gold">{t('use.notFound')}</span>
          {isLast && onDeep && <button type="button" onClick={onDeep} className="text-[13px] text-cream-secondary underline hover:text-cream">{t('use.deep')}</button>}
        </div>
      )}
      {hasText && !live && (
        <div className={`mt-2 flex items-center gap-0.5 ${isLast ? '' : 'opacity-0 group-hover/answer:opacity-100 focus-within:opacity-100'}`}>
          <ActionButton icon="copy" label={t('copy')} onClick={() => copyText(t, answer.content)} />
          {isLast && onRegenerate && <ActionButton icon="refresh" label={t('regenerate')} onClick={onRegenerate} />}
          {rateable && <FeedbackButtons messageId={answer.id} initial={answer.feedback} />}
        </div>
      )}
      {isLast && !live && answer.follow_ups?.length > 0 && (
        <div className="mt-5">
          <div className="mb-1 text-[13px] text-cream-muted">{t('followups')}</div>
          {answer.follow_ups.map((f) => (
            <button key={f} type="button" onClick={() => onFollowUp(f)}
              className="flex w-full items-center gap-2 border-t border-border-subtle py-2.5 text-left text-[14.5px] text-cream-secondary hover:text-cream">
              <Icon name="chevronRight" className="h-4 w-4 shrink-0 text-cream-muted" />
              <span>{f}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
});

/* ── The question ── */

function AttachmentThumb({ item }) {
  const [src, setSrc] = useState(null);
  useEffect(() => {
    if (item.thumb) { setSrc(item.thumb); return undefined; }
    if (item.kind !== 'file' || !/\.(png|jpe?g|gif|webp)$/i.test(item.name || '')) return undefined;
    let url;
    let current = true;
    api.raw(`/api/files/${item.id}/content`).then(async (r) => {
      if (!r.ok || !current) return;
      url = URL.createObjectURL(await r.blob());
      if (current) setSrc(url);
    }).catch(() => {});
    return () => { current = false; if (url) URL.revokeObjectURL(url); };
  }, [item]);
  return src ? <img src={src} alt={item.name} className="h-20 max-w-[160px] rounded-xl object-cover" /> : null;
}

export function Question({ question, canEdit, onEdit, versions }) {
  const t = useT();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(question.content);
  const attachments = question.attachments || [];
  const images = attachments.filter((a) => a.thumb || (a.kind === 'file' && /\.(png|jpe?g|gif|webp)$/i.test(a.name || '')));
  const others = attachments.filter((a) => !images.includes(a));
  if (editing) {
    return (
      <div className="ml-auto w-full max-w-[75%] rounded-2xl bg-ink-hover p-3">
        <textarea autoFocus value={text} onChange={(e) => setText(e.target.value)} rows={3} aria-label={t('edit')}
          className="w-full resize-y bg-transparent text-[15.5px] text-cream outline-none" />
        <div className="mt-2 flex justify-end gap-2">
          <button type="button" onClick={() => { setEditing(false); setText(question.content); }}
            className="rounded-full px-3 py-1.5 text-[13px] text-cream-secondary hover:bg-ink-elevated">{t('cancel')}</button>
          <button type="button" disabled={!text.trim()} onClick={() => { setEditing(false); onEdit(text); }}
            className="rounded-full bg-ink px-3 py-1.5 text-[13px] text-on-ink disabled:opacity-40">{t('send')}</button>
        </div>
      </div>
    );
  }
  return (
    <div className="group/question flex flex-col items-end">
      {images.length > 0 && <div className="mb-1.5 flex flex-wrap justify-end gap-2">{images.map((a) => <AttachmentThumb key={a.id} item={a} />)}</div>}
      {others.length > 0 && (
        <div className="mb-1.5 flex flex-wrap justify-end gap-1.5">
          {others.map((a) => (
            <span key={`${a.kind}-${a.id}`} className="inline-flex max-w-[220px] items-center gap-1.5 rounded-lg border border-border-default px-2 py-1 text-[12.5px] text-cream-secondary">
              <Icon name={a.kind === 'document' ? 'book' : a.kind === 'session' ? 'message' : 'file'} className={`h-3.5 w-3.5 ${a.kind === 'document' ? 'text-cyan' : ''}`} />
              <span className="truncate">{a.name}</span>
            </span>
          ))}
        </div>
      )}
      {question.content && (
        <div className="max-w-[75%] whitespace-pre-wrap break-words rounded-[20px] bg-ink-hover px-4 py-2.5 text-[15.5px] leading-relaxed text-cream">
          {question.content}
        </div>
      )}
      <div className="mt-1 flex items-center gap-0.5">
        {versions}
        <span className="flex items-center gap-0.5 opacity-0 group-hover/question:opacity-100 focus-within:opacity-100">
        {canEdit && <ActionButton icon="pencil" label={t('edit')} onClick={() => setEditing(true)} />}
        <ActionButton icon="copy" label={t('copy')} onClick={() => copyText(t, question.content)} />
        </span>
      </div>
    </div>
  );
}

export { VersionSwitch };
