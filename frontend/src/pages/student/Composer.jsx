/**
 * Composer.jsx — the input card of the student chat (2026-11 redesign).
 *
 * Chips (attachments, tools turned on), the text box (Enter sends, Shift+Enter
 * new line, a pasted image is uploaded), and the "+" menu: upload a file, take
 * a screenshot, attach a course file / an earlier chat / an earlier upload,
 * and the switches for web search, Python and deep research. A switch the
 * teacher turned off, or any of them in exam mode, is locked.
 *
 * Uploads go to POST /api/student/files at once (the text is read there); the
 * question sends their ids (chat attachments). The page drops files on the
 * chat pane through the `addFiles` handle.
 */
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react';
import api from '../../lib/api';
import Icon from '../../components/Icon';
import { useT } from '../../lib/i18n';
import { messageLength } from '../../lib/historyLine';
import { MenuItem, Switch, menuClass, useDismiss } from './ui';

export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
const ACCEPT = 'image/*,.pdf,.txt,.md,.py,.c,.h,.cpp,.java,.js,.ts,.sql,.csv,.json,.html,.css,.ipynb,.r,.m,.sh';
const TOOLS = [
  { key: 'web', icon: 'globe', label: 'tool.web' },
  { key: 'code', icon: 'terminal', label: 'tool.code' },
  { key: 'deep', icon: 'deep', label: 'tool.deep', caption: 'tool.deepCaption' },
];

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** One frame of a screen the student picks (desktop browsers), as a PNG file. */
async function screenshot() {
  const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
  try {
    const video = document.createElement('video');
    video.srcObject = stream;
    video.muted = true;
    await video.play();
    await new Promise((r) => setTimeout(r, 300));
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);
    const blob = await new Promise((r) => canvas.toBlob(r, 'image/png'));
    return new File([blob], `capture-${new Date().toISOString().slice(11, 19).replace(/:/g, '')}.png`, { type: 'image/png' });
  } finally {
    stream.getTracks().forEach((track) => track.stop());
  }
}

function Chip({ chip, onRemove }) {
  const t = useT();
  const course = chip.kind === 'document';
  return (
    <span className={`inline-flex max-w-[240px] items-center gap-2 rounded-xl border px-2 py-1.5 text-[13px] ${
      chip.error ? 'border-danger/40 text-danger' : 'border-border-default text-cream'}`}
      title={chip.error || (course ? t('attach.onlyThisFile') : chip.name)}>
      {chip.thumb
        ? <img src={chip.thumb} alt="" className="h-8 w-8 shrink-0 rounded-md object-cover" />
        : <Icon name={course ? 'book' : chip.kind === 'session' ? 'message' : 'file'}
            className={`h-4 w-4 shrink-0 ${course ? 'text-cyan' : 'text-cream-secondary'}`} />}
      <span className="min-w-0">
        <span className="block truncate">{chip.kind === 'session' ? t('attach.chat', { name: chip.name }) : chip.name}</span>
        <span className="block truncate text-[11.5px] text-cream-muted">
          {chip.uploading ? t('attach.uploading') : chip.error ? chip.error
            : course ? t('attach.onlyThisFile') : chip.size ? formatSize(chip.size) : ''}
        </span>
      </span>
      <button type="button" onClick={onRemove} aria-label={t('remove')}
        className="shrink-0 rounded p-0.5 text-cream-muted hover:bg-ink-hover hover:text-cream">
        <Icon name="close" className="h-3.5 w-3.5" />
      </button>
    </span>
  );
}

function AttachMenu({ labId, sessions, activeSessionId, examMode, uploadOk, chips, onToggle, onBack }) {
  const t = useT();
  const [docs, setDocs] = useState(null);
  const [files, setFiles] = useState(null);
  useEffect(() => {
    if (!labId) return;
    api.get(`/api/student/labs/${labId}/documents`).then(setDocs).catch(() => setDocs([]));
    if (uploadOk) api.get(`/api/student/files?lab_id=${labId}`).then(setFiles).catch(() => setFiles([]));
  }, [labId, uploadOk]);
  const chosen = (kind, id) => chips.some((c) => c.kind === kind && c.id === id);
  const earlier = examMode ? [] : sessions.filter((s) => s.id !== activeSessionId).slice(0, 5);
  const group = 'px-2.5 pb-1 pt-2 text-[12px] font-medium text-cream-muted';
  return (
    <div>
      {onBack && <MenuItem icon="chevronLeft" label={t('back')} onClick={onBack} />}
      <div className={group}>{t('attach.courseFiles')}</div>
      {docs === null && <p className="px-2.5 py-1 text-[13px] text-cream-muted">…</p>}
      {docs?.length === 0 && <p className="px-2.5 py-1 text-[13px] text-cream-muted">{t('attach.noCourseFiles')}</p>}
      {docs?.map((d) => (
        <MenuItem key={d.id} icon="book" label={d.filename} hint={d.scope === 'class' ? t('scope.class') : null}
          onClick={() => onToggle({ kind: 'document', id: d.id, name: d.filename })}>
          {chosen('document', d.id) && <Icon name="check" className="h-4 w-4 text-cyan" />}
        </MenuItem>
      ))}
      {earlier.length > 0 && <div className={group}>{t('attach.earlierChats')}</div>}
      {earlier.map((s) => (
        <MenuItem key={s.id} icon="message" label={s.title || t('session.untitled')}
          onClick={() => onToggle({ kind: 'session', id: s.id, name: s.title || t('session.untitled') })}>
          {chosen('session', s.id) && <Icon name="check" className="h-4 w-4 text-cream-secondary" />}
        </MenuItem>
      ))}
      {uploadOk && files?.length > 0 && <div className={group}>{t('attach.myFiles')}</div>}
      {uploadOk && files?.slice(0, 6).map((f) => (
        <MenuItem key={f.id} icon={f.kind === 'image' ? 'image' : 'file'} label={f.filename}
          onClick={() => onToggle({ kind: 'file', id: f.id, name: f.filename, size: f.size })}>
          {chosen('file', f.id) && <Icon name="check" className="h-4 w-4 text-cream-secondary" />}
        </MenuItem>
      ))}
    </div>
  );
}

const Composer = forwardRef(function Composer({
  value, onChange, onSend, onStop, streaming, labId, features, examMode, sessions, activeSessionId,
  tools, setTools, disabledReason, inputRef, centered,
}, handle) {
  const t = useT();
  const [chips, setChips] = useState([]);
  const [menu, setMenu] = useState(null);       // null | 'main' | 'attach'
  const menuRef = useRef(null);
  const fileRef = useRef(null);
  useDismiss(menuRef, !!menu, () => setMenu(null));
  const length = messageLength(value);
  const uploadOk = !!features?.upload && !examMode;
  const narrow = typeof window !== 'undefined' && window.innerWidth < 640;

  const upload = useCallback(async (file) => {
    const key = `up-${Math.random().toString(36).slice(2)}`;
    const thumb = file.type.startsWith('image/') ? URL.createObjectURL(file) : null;
    if (file.size > MAX_UPLOAD_BYTES) {
      setChips((c) => [...c, { key, kind: 'file', name: file.name, size: file.size, thumb, error: t('attach.tooBig') }]);
      return;
    }
    setChips((c) => [...c, { key, kind: 'file', name: file.name, size: file.size, thumb, uploading: true }]);
    const form = new FormData();
    form.append('file', file);
    form.append('lab_id', labId);
    try {
      const row = await api.upload('/api/student/files', form);
      setChips((c) => c.map((x) => (x.key === key ? { ...x, id: row.id, uploading: false, name: row.filename } : x)));
    } catch (err) {
      const known = ['file_too_large', 'unsupported_type', 'empty_file', 'unreadable_pdf', 'feature_off', 'lab_locked'];
      const message = known.includes(err.message) ? t(`upload.${err.message}`) : t('attach.failed');
      setChips((c) => c.map((x) => (x.key === key ? { ...x, uploading: false, error: message } : x)));
    }
  }, [labId, t]);

  const addFiles = useCallback((list) => {
    if (!uploadOk) return false;
    [...list].forEach(upload);
    return true;
  }, [upload, uploadOk]);
  useImperativeHandle(handle, () => ({ addFiles, clear: () => setChips([]) }), [addFiles]);
  useEffect(() => { setChips([]); }, [labId, activeSessionId]);

  // Autogrow up to about 8 lines.
  useEffect(() => {
    const box = inputRef.current;
    if (!box) return;
    box.style.height = 'auto';
    box.style.height = `${Math.min(box.scrollHeight, 224)}px`;
  }, [value, inputRef]);

  const toggleChip = (chip) => setChips((c) => (c.some((x) => x.kind === chip.kind && x.id === chip.id)
    ? c.filter((x) => !(x.kind === chip.kind && x.id === chip.id)) : [...c, { ...chip, key: `${chip.kind}-${chip.id}` }]));
  const ready = chips.every((c) => !c.uploading);
  const usable = chips.filter((c) => c.id && !c.error);
  const blocked = !!disabledReason;
  const canSend = !blocked && !length.over && ready && (value.trim() || usable.length) && !streaming;
  const send = () => {
    if (!canSend) return;
    onSend(value, usable.map((c) => ({ kind: c.kind, id: c.id, name: c.name, size: c.size, thumb: c.thumb })));
    setChips([]);
  };
  const toolOff = (key) => !features?.[key] || examMode;
  const placeholder = disabledReason ? t(`input.${disabledReason}`)
    : examMode ? t('input.exam') : t('input.placeholder');

  return (
    <div className={centered ? '' : 'pb-3'}>
      <div className="rounded-[24px] border border-border-default bg-ink-raised shadow-input" data-tour="ask">
        {(chips.length > 0 || tools.length > 0) && (
          <div className="flex flex-wrap gap-2 px-3 pt-3">
            {chips.map((c) => <Chip key={c.key} chip={c} onRemove={() => setChips((x) => x.filter((y) => y.key !== c.key))} />)}
            {tools.map((key) => {
              const tool = TOOLS.find((x) => x.key === key);
              return (
                <span key={key} className="inline-flex items-center gap-1.5 self-center rounded-full bg-ink-surface py-1 pl-2.5 pr-1.5 text-[13px] text-cream">
                  <Icon name={tool.icon} className="h-3.5 w-3.5 text-cream-secondary" />
                  {t(tool.label)}
                  <button type="button" aria-label={t('remove')} onClick={() => setTools(tools.filter((x) => x !== key))}
                    className="rounded-full p-0.5 text-cream-muted hover:bg-ink-hover hover:text-cream">
                    <Icon name="close" className="h-3 w-3" />
                  </button>
                </span>
              );
            })}
          </div>
        )}
        <textarea ref={inputRef} id="chat-input" rows={1} value={value} disabled={blocked}
          onChange={(e) => onChange(e.target.value)} placeholder={placeholder} aria-label={t('input.label')}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }}
          onPaste={(e) => {
            const images = [...(e.clipboardData?.files || [])].filter((f) => f.type.startsWith('image/'));
            if (images.length && addFiles(images)) e.preventDefault();
          }}
          className="block max-h-56 w-full resize-none bg-transparent px-4 pb-1 pt-3.5 text-[16px] leading-relaxed text-cream outline-none placeholder:text-cream-muted disabled:cursor-not-allowed" />
        <div className="flex items-center gap-2 px-2.5 pb-2.5 pt-1">
          <div ref={menuRef} className="relative" data-tour="plus">
            <button type="button" onClick={() => setMenu(menu ? null : 'main')} aria-label={t('plus.open')} aria-expanded={!!menu}
              disabled={blocked}
              className="flex h-8 w-8 items-center justify-center rounded-full border border-border-default text-cream-secondary hover:bg-ink-hover hover:text-cream disabled:opacity-40">
              <Icon name="plus" className="h-[18px] w-[18px]" />
            </button>
            {menu && (
              <div className={`absolute bottom-full left-0 z-30 mb-2 w-[280px] ${menuClass}`}>
                {menu === 'attach' && narrow ? (
                  <AttachMenu labId={labId} sessions={sessions} activeSessionId={activeSessionId} examMode={examMode}
                    uploadOk={uploadOk} chips={chips} onToggle={toggleChip} onBack={() => setMenu('main')} />
                ) : (
                  <>
                    <MenuItem icon="paperclip" label={t('plus.upload')} disabled={!uploadOk}
                      title={uploadOk ? undefined : t('feature.locked')}
                      onClick={() => { setMenu(null); fileRef.current?.click(); }} />
                    <MenuItem icon="camera" label={t('plus.screenshot')}
                      disabled={!uploadOk || !navigator.mediaDevices?.getDisplayMedia}
                      onClick={async () => {
                        setMenu(null);
                        try { addFiles([await screenshot()]); } catch { /* the student cancelled */ }
                      }} />
                    <div className="relative">
                      <MenuItem icon="file" label={t('plus.attach')} onClick={() => setMenu(menu === 'attach' ? 'main' : 'attach')}>
                        <Icon name="chevronRight" className="h-4 w-4 text-cream-muted" />
                      </MenuItem>
                      {menu === 'attach' && !narrow && (
                        <div className={`absolute bottom-0 left-full z-40 ml-2 max-h-[60vh] w-[280px] overflow-y-auto ${menuClass}`}>
                          <AttachMenu labId={labId} sessions={sessions} activeSessionId={activeSessionId} examMode={examMode}
                            uploadOk={uploadOk} chips={chips} onToggle={toggleChip} />
                        </div>
                      )}
                    </div>
                    <div className="my-1 border-t border-border-subtle" />
                    {TOOLS.map((tool) => {
                      const off = toolOff(tool.key);
                      const on = tools.includes(tool.key);
                      return (
                        <label key={tool.key} htmlFor={`tool-${tool.key}`}
                          className={`flex min-h-9 items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[14px] ${off ? 'opacity-50' : 'cursor-pointer hover:bg-ink-hover'}`}>
                          <Icon name={off ? 'lock' : tool.icon} className="h-4 w-4 shrink-0 text-cream-secondary" />
                          <span className="min-w-0 flex-1">
                            <span className="block text-cream">{t(tool.label)}</span>
                            {(off || tool.caption) && (
                              <span className="block text-[12px] text-cream-muted">
                                {off ? t(examMode ? 'feature.exam' : 'feature.teacherOff') : t(tool.caption)}
                              </span>
                            )}
                          </span>
                          <Switch id={`tool-${tool.key}`} checked={on && !off} disabled={off} label={t(tool.label)}
                            onChange={(next) => setTools(next ? [...tools, tool.key] : tools.filter((x) => x !== tool.key))} />
                        </label>
                      );
                    })}
                  </>
                )}
              </div>
            )}
            <input ref={fileRef} type="file" multiple accept={ACCEPT} className="hidden"
              onChange={(e) => { addFiles(e.target.files || []); e.target.value = ''; }} />
          </div>
          {examMode && <span className="rounded-full bg-warn-soft px-2.5 py-0.5 text-[12px] text-gold">{t('exam.short')}</span>}
          <div className="flex-1" />
          {length.count && <span className={`font-mono text-[12px] tabular-nums ${length.over ? 'text-danger' : 'text-gold'}`}>{length.count}</span>}
          {streaming ? (
            <button type="button" onClick={onStop} aria-label={t('stop')} title={t('stop')}
              className="flex h-8 w-8 items-center justify-center rounded-full bg-ink text-on-ink">
              <span className="h-3 w-3 rounded-[3px] bg-on-ink" />
            </button>
          ) : (
            <button type="button" onClick={send} disabled={!canSend} aria-label={t('send')} title={t('send')}
              className="flex h-8 w-8 items-center justify-center rounded-full bg-ink text-on-ink disabled:opacity-30">
              <Icon name="arrowUp" className="h-[18px] w-[18px]" />
            </button>
          )}
        </div>
      </div>
      {length.over ? (
        <p className="mt-2 px-2 text-center text-[12.5px] text-danger" role="alert">
          {t('input.tooLong', { n: length.excess.toLocaleString() })}
        </p>
      ) : !centered && (
        <p className="mt-2 px-2 text-center text-[12px] text-cream-muted">{t('input.note')}</p>
      )}
    </div>
  );
});

export default Composer;
