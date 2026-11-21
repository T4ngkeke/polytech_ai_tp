/**
 * RuleList.jsx — the rules a teacher gives the tutor, one per line.
 *
 * For a class, a lab, or a student in one class (classId). Each rule can be
 * switched off, edited or deleted (with Undo). Below the list, buttons add
 * rules: ready-made ones and the teacher's saved rules ("My rules") work as
 * switches — a click adds the rule, a second click removes it. "+ Custom rule"
 * opens an editor; the new rule is saved in My rules unless unticked. A rule
 * remembers its button through `source` ('b:<key>' or 'm:<saved rule id>').
 * The active rules go into the prompt as a numbered list (backend rule_service).
 *
 * After a rule is added, switched on or edited, the model checks the active
 * rules of this list for pairs that contradict (POST /api/teacher/rule-items/check).
 * A pair is shown as a warning: the teacher reviews the two rules, or ignores
 * the warning (remembered in this browser by the two texts). Switching a rule
 * off or deleting it removes its warning without a model call.
 *
 * Data: GET/POST /api/teacher/rule-items, PATCH/DELETE /api/teacher/rule-items/{id},
 * GET /api/teacher/rule-presets (ready-made), /api/teacher/skill-presets (My rules).
 * The interaction was agreed on a prototype first (2026-11-10; the check 2026-11-17).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';

import api from '../lib/api';
import {
  autoName, conflictKey, conflictNumbers, liveConflicts, loadIgnored, saveIgnored,
} from '../lib/rules';
import Icon from './Icon';
import { Switch } from '../pages/student/ui';
import { t as tr, useT } from '../lib/i18n';

const MAX_RULES = 30;
const ONLY_HERE = { class: 't.rl.only.class', lab: 't.rl.only.lab', student: 't.rl.only.student' };

function undoToast(message, undo) {
  toast((t) => (
    <span className="flex items-center gap-3">
      <span>{message}</span>
      {undo && (
        <button type="button" className="cursor-pointer font-semibold text-cream underline underline-offset-2"
          onClick={() => { toast.dismiss(t.id); undo(); }}>
          {tr('t.undo')}
        </button>
      )}
    </span>
  ), { duration: 6000 });
}

const short = (text) => (text.length > 40 ? `${text.slice(0, 40)}…` : text);

export default function RuleList({ level, targetId, classId, inherited, inheritedLabel }) {
  const t = useT();
  const [items, setItems] = useState(null);
  const [builtIn, setBuiltIn] = useState([]);
  const [mine, setMine] = useState([]);
  const [busy, setBusy] = useState(false);
  const [editingRule, setEditingRule] = useState(null);  // { id, text }
  const [editor, setEditor] = useState(null);            // { mode: 'new'|'edit', id?, text, name, save }
  // The contradiction check: idle | checking | done | unavailable
  const [check, setCheck] = useState({ state: 'idle', conflicts: [] });
  const [review, setReview] = useState(null);            // the key of the conflict being reviewed
  const [showIgnored, setShowIgnored] = useState(false);
  const checkRun = useRef(0);

  const query = `level=${level}&target_id=${targetId}${level === 'student' ? `&class_id=${classId}` : ''}`;
  const listKey = `${level}:${targetId}${level === 'student' ? `:${classId}` : ''}`;
  const [ignored, setIgnored] = useState(() => loadIgnored(listKey));
  useEffect(() => { setIgnored(loadIgnored(listKey)); setCheck({ state: 'idle', conflicts: [] }); setReview(null); }, [listKey]);
  const ignore = (keys) => { setIgnored(keys); saveIgnored(listKey, keys); };

  // Only the latest check counts: a slower earlier answer is dropped.
  const runCheck = useCallback(async () => {
    const run = ++checkRun.current;
    setCheck((prev) => ({ ...prev, state: 'checking' }));
    try {
      const body = { level, target_id: targetId, ...(level === 'student' ? { class_id: classId } : {}) };
      const { conflicts } = await api.post('/api/teacher/rule-items/check', body);
      if (run === checkRun.current) setCheck({ state: 'done', conflicts });
    } catch {
      if (run === checkRun.current) setCheck({ state: 'unavailable', conflicts: [] });
    }
  }, [level, targetId, classId]);
  const load = useCallback(async () => {
    try { setItems(await api.get(`/api/teacher/rule-items?${query}`)); }
    catch (err) { setItems([]); toast.error(err.message); }
  }, [query]);
  const loadMine = useCallback(() => {
    api.get('/api/teacher/skill-presets').then(setMine).catch(() => {});
  }, []);
  useEffect(() => { setItems(null); setEditor(null); setEditingRule(null); load(); }, [load]);
  useEffect(() => {
    api.get('/api/teacher/rule-presets').then(setBuiltIn).catch(() => {});
    loadMine();
    // Another list on the page (the student window) may change My rules.
    window.addEventListener('edullm-presets-changed', loadMine);
    return () => window.removeEventListener('edullm-presets-changed', loadMine);
  }, [loadMine]);
  const minesChanged = () => window.dispatchEvent(new Event('edullm-presets-changed'));

  const buttons = [
    ...builtIn.map((p) => ({ source: `b:${p.key}`, name: p.name, text: p.text, group: t('t.rl.readyMade') })),
    ...mine.map((p) => ({ source: `m:${p.id}`, name: p.name, text: p.content, group: t('t.rl.mine'), preset: p })),
  ];
  const origin = (source) => {
    const b = buttons.find((x) => x.source === source);
    return b ? `${b.group} · ${b.name}` : source ? t('t.rl.mine') : t(ONLY_HERE[level]);
  };
  const inList = (source) => items?.find((r) => r.source === source);

  const add = async (text, source = null, extra = {}) => {
    setBusy(true);
    try {
      const created = await api.post('/api/teacher/rule-items', {
        level, target_id: targetId, ...(level === 'student' ? { class_id: classId } : {}),
        text: text.trim(), source, ...extra,
      });
      await load();
      if (created.is_active) runCheck();
      return created;
    } catch (err) {
      toast.error(err.message === 'too_many_rules' ? t('t.rl.max', { n: MAX_RULES }) : err.message);
      return null;
    } finally {
      setBusy(false);
    }
  };

  const patch = async (item, body) => {
    try {
      const updated = await api.patch(`/api/teacher/rule-items/${item.id}`, body);
      setItems((prev) => prev.map((r) => (r.id === item.id ? updated : r)));
      if (updated.is_active && (body.is_active === true || body.text !== undefined)) runCheck();
      return true;
    } catch (err) {
      toast.error(err.message);
      return false;
    }
  };

  // Delete at once; Undo puts the rule back in its place.
  const remove = async (item) => {
    const position = items.findIndex((r) => r.id === item.id);
    try {
      await api.delete(`/api/teacher/rule-items/${item.id}`);
      const left = items.filter((r) => r.id !== item.id);
      setItems(left);
      // The reasons name rule numbers, which move after a delete.
      if (liveConflicts(check.conflicts, left).length) runCheck();
      undoToast(t('t.rl.removed', { text: short(item.text) }), () => add(item.text, item.source,
        { is_active: item.is_active, position }));
    } catch (err) { toast.error(err.message); }
  };

  // A button is a switch: in the list or not.
  const toggle = (b) => {
    const found = inList(b.source);
    if (found) remove(found);
    else add(b.text, b.source);
  };

  const saveEditor = async () => {
    const text = editor.text.trim();
    if (!text) return;
    const name = editor.name.trim() || autoName(text);
    setBusy(true);
    try {
      if (editor.mode === 'edit') {
        await api.put(`/api/teacher/skill-presets/${editor.id}`, { name, content: text });
        toast.success(t('t.rl.saved', { name }));
        loadMine(); minesChanged(); await load();
        if ((items || []).some((r) => r.source === `m:${editor.id}` && r.is_active)) runCheck();
      } else if (editor.save) {
        const preset = await api.post('/api/teacher/skill-presets', { name, content: text });
        loadMine(); minesChanged();
        if (await add(text, `m:${preset.id}`)) toast.success(t('t.rl.addedSaved'));
      } else if (await add(text, null)) {
        toast.success(t('t.rl.added', { where: t(ONLY_HERE[level]) }));
      }
      setEditor(null);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };

  // Rules added from it stay, unlinked; Undo saves it again and links them back.
  const deleteMine = async (preset) => {
    const linked = (items || []).filter((r) => r.source === `m:${preset.id}`);
    try {
      await api.delete(`/api/teacher/skill-presets/${preset.id}`);
      setEditor(null);
      loadMine(); minesChanged(); await load();
      undoToast(t('t.rl.presetRemoved', { name: preset.name }), async () => {
        const again = await api.post('/api/teacher/skill-presets', { name: preset.name, content: preset.content });
        await Promise.all(linked.map((r) => api.patch(`/api/teacher/rule-items/${r.id}`, { source: `m:${again.id}` })));
        loadMine(); minesChanged(); load();
      });
    } catch (err) { toast.error(err.message); }
  };

  const saveRule = async (item) => {
    if (editingRule.text.trim() && await patch(item, { text: editingRule.text.trim() })) setEditingRule(null);
  };

  if (items === null) return <p className="text-[13px] text-cream-muted">{t('t.rl.loading')}</p>;
  const active = items.filter((r) => r.is_active).length;
  const full = items.length >= MAX_RULES;
  const live = liveConflicts(check.conflicts, items).map((c) => ({ ...c, key: conflictKey(items, c) }));
  const shown = live.filter((c) => !ignored.includes(c.key));
  const hidden = live.filter((c) => ignored.includes(c.key));
  const reviewed = shown.find((c) => c.key === review);
  const flagged = new Set((reviewed ? [reviewed] : shown).flatMap((c) => c.rule_ids));
  return (
    <div className="space-y-4">
      {inherited?.length > 0 && (
        <div>
          <p className="mb-2 text-[13px] font-medium text-cream-muted">{inheritedLabel}</p>
          <ol className="space-y-1">
            {inherited.map((r, i) => (
              <li key={r.id} className="flex gap-2 rounded-lg bg-ink-base px-3 py-2 text-[13px] text-cream-secondary">
                <span className="font-mono text-cream-muted">{i + 1}.</span><span className="whitespace-pre-wrap">{r.text}</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      <div>
        <div className="mb-2 flex items-baseline justify-between">
          <p className="text-[15px] font-semibold text-cream">{t('tab.rules')}</p>
          <span className="text-[12.5px] text-cream-muted">
            {items.length ? t('t.rl.count', { on: active, n: items.length, max: MAX_RULES }) : t('t.rl.none')}
          </span>
        </div>
        {items.length === 0 ? (
          <p className="text-[13px] text-cream-muted">{t('t.rl.noneHelp')}</p>
        ) : (
          <ol className="space-y-1.5">
            {items.map((r, i) => (
              <li key={r.id} id={`rule-${r.id}`} className={`flex items-start gap-3 rounded-xl border px-3 py-2 transition-shadow ${
                !r.is_active ? 'border-border-subtle bg-ink-base'
                  : flagged.has(r.id) ? 'border-gold/50 bg-ink-raised ring-2 ring-gold-muted' : 'border-border-default bg-ink-raised'}`}>
                <span className="mt-0.5 w-5 shrink-0 text-right font-mono text-[13px] text-cream-muted">{i + 1}.</span>
                {editingRule?.id === r.id ? (
                  <div className="flex-1 space-y-2">
                    <textarea value={editingRule.text} autoFocus rows={2} aria-label={t('t.rl.ruleText')}
                      onChange={(e) => setEditingRule({ ...editingRule, text: e.target.value })}
                      onKeyDown={(e) => {
                        if (e.key === 'Escape') setEditingRule(null);
                        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveRule(r); }
                      }}
                      className="w-full resize-y rounded-lg border border-border-default bg-ink-deep px-3 py-2 text-sm text-cream focus:border-cream-muted focus:outline-none" />
                    <div className="flex gap-2">
                      <button type="button" disabled={!editingRule.text.trim()} onClick={() => saveRule(r)}
                        className="cursor-pointer rounded-full bg-ink px-3.5 py-1.5 text-[13px] text-on-ink disabled:opacity-40">{t('save')}</button>
                      <button type="button" onClick={() => setEditingRule(null)}
                        className="cursor-pointer rounded-lg px-3 py-1.5 text-[13px] text-cream-secondary hover:bg-ink-hover">{t('cancel')}</button>
                    </div>
                  </div>
                ) : (
                  <div className="min-w-0 flex-1">
                    <p className={`whitespace-pre-wrap text-[14px] ${r.is_active ? 'text-cream' : 'text-cream-muted line-through'}`}>{r.text}</p>
                    <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[12px] text-cream-muted">
                      <span>{origin(r.source)}</span>
                      {flagged.has(r.id) && r.is_active && (
                        <span className="inline-flex items-center gap-1 text-gold">
                          <Icon name="warning" className="h-3 w-3" />{t('t.rl.mayContradict')}
                        </span>
                      )}
                    </p>
                  </div>
                )}
                {editingRule?.id !== r.id && (
                  <div className="flex shrink-0 items-center gap-0.5">
                    <span className="mx-1 flex"><Switch checked={r.is_active} label={t(r.is_active ? 't.rl.switchOff' : 't.rl.switchOn')}
                      onChange={() => patch(r, { is_active: !r.is_active })} /></span>
                    <IconBtn title={t('edit')} onClick={() => setEditingRule({ id: r.id, text: r.text })}><Icon name="pencil" className="h-3.5 w-3.5" /></IconBtn>
                    <IconBtn title={t('delete')} danger onClick={() => remove(r)}><Icon name="trash" className="h-3.5 w-3.5" /></IconBtn>
                  </div>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>

      <ConflictPanel check={check} items={items} shown={shown} hidden={hidden} reviewed={reviewed}
        showIgnored={showIgnored} setShowIgnored={setShowIgnored}
        onReview={(c) => {
          setReview(c.key);
          document.getElementById(`rule-${c.rule_ids[0]}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
        }}
        onDone={() => setReview(null)}
        onIgnore={(c) => {
          ignore([...ignored, c.key]);
          setReview(null);
          undoToast(t('t.rl.warnIgnored'), () => ignore(ignored.filter((k) => k !== c.key)));
        }}
        onWarnAgain={(c) => ignore(ignored.filter((k) => k !== c.key))} />

      <div className="rounded-2xl border border-border-subtle bg-ink-raised p-4 space-y-4">
        <Group title={t('t.rl.readyMadeTitle')} note={t('t.rl.readyMadeNote')}>
          {buttons.filter((b) => !b.preset).map((b) => (
            <RuleButton key={b.source} b={b} found={inList(b.source)} disabled={busy || (full && !inList(b.source))}
              onClick={() => toggle(b)} />
          ))}
        </Group>
        <Group title={t('t.rl.mine')} note={t('t.rl.mineNote')}>
          {buttons.filter((b) => b.preset).map((b) => (
            <span key={b.source} className="inline-flex items-stretch">
              <RuleButton b={b} found={inList(b.source)} joined disabled={busy || (full && !inList(b.source))}
                onClick={() => toggle(b)} />
              <button type="button" title={t('t.rl.editSaved')} aria-label={t('t.rl.editName', { name: b.name })}
                onClick={() => setEditor({ mode: 'edit', id: b.preset.id, text: b.preset.content, name: b.preset.name, save: true })}
                className={`cursor-pointer rounded-r-full border border-l-0 px-2 transition-colors ${inList(b.source)
                  ? 'border-ink bg-ink text-on-ink' : 'border-border-default text-cream-muted hover:text-cream'}`}>
                <Icon name="pencil" className="h-3 w-3" />
              </button>
            </span>
          ))}
          <button type="button" disabled={full} onClick={() => setEditor({ mode: 'new', text: '', name: '', save: true })}
            className="cursor-pointer rounded-full border border-dashed border-border-default px-2.5 py-1 text-[13px] text-cream-secondary hover:bg-ink-hover hover:text-cream disabled:opacity-40">
            + {t('t.rl.custom')}
          </button>
        </Group>

        {editor && (
          <div className="space-y-2 rounded-xl border border-border-default bg-ink-base p-3">
            <label className="block text-[13px] text-cream-secondary" htmlFor="rule-editor-text">
              {t(editor.mode === 'edit' ? 't.rl.editYours' : 't.rl.yours')}
            </label>
            <textarea id="rule-editor-text" rows={3} autoFocus value={editor.text}
              placeholder={t('t.rl.example')}
              onChange={(e) => setEditor({ ...editor, text: e.target.value })}
              onKeyDown={(e) => { if (e.key === 'Escape') setEditor(null); }}
              className="w-full resize-y rounded-lg border border-border-default bg-ink-deep px-3 py-2 text-[14px] text-cream placeholder:text-cream-muted focus:border-cream-muted focus:outline-none" />
            {(editor.save || editor.mode === 'edit') && (
              <>
                <label className="block text-[13px] text-cream-secondary" htmlFor="rule-editor-name">{t('t.rl.shortName')}</label>
                <input id="rule-editor-name" type="text" value={editor.name}
                  placeholder={editor.text.trim() ? autoName(editor.text) : t('t.rl.nameAuto')}
                  onChange={(e) => setEditor({ ...editor, name: e.target.value })}
                  className="w-full rounded-lg border border-border-default bg-ink-deep px-3 py-1.5 text-[14px] text-cream placeholder:text-cream-muted focus:border-cream-muted focus:outline-none" />
              </>
            )}
            {editor.mode === 'new' && (
              <label className="flex cursor-pointer items-center gap-2 text-[13px] text-cream-secondary" htmlFor="rule-editor-save">
                <input id="rule-editor-save" type="checkbox" checked={editor.save}
                  onChange={(e) => setEditor({ ...editor, save: e.target.checked })} className="h-4 w-4 accent-[rgb(var(--ink))]" />
                {t('t.rl.alsoSave')}
              </label>
            )}
            {editor.mode === 'edit' && (
              <p className="text-[12px] text-cream-muted">{t('t.rl.editNote')}</p>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" disabled={busy || !editor.text.trim()} onClick={saveEditor}
                className="cursor-pointer rounded-full bg-ink px-3.5 py-1.5 text-[13px] text-on-ink disabled:opacity-40">
                {editor.mode === 'edit' ? t('save') : t('t.rl.add')}
              </button>
              <button type="button" onClick={() => setEditor(null)}
                className="cursor-pointer rounded-lg px-3 py-1.5 text-[13px] text-cream-secondary hover:bg-ink-hover">{t('cancel')}</button>
              {editor.mode === 'edit' && (
                <button type="button" onClick={() => deleteMine(mine.find((m) => m.id === editor.id))}
                  className="ml-auto cursor-pointer rounded-lg px-3 py-1.5 text-[13px] text-danger hover:bg-danger-muted">
                  {t('t.rl.deleteSaved')}
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function ConflictPanel({ check, items, shown, hidden, reviewed, showIgnored, setShowIgnored,
  onReview, onDone, onIgnore, onWarnAgain }) {
  const t = useT();
  if (check.state === 'checking') {
    return <p className="text-[13px] text-cream-muted" role="status">{t('t.rl.checking')}</p>;
  }
  if (check.state === 'unavailable') {
    return <p className="text-[13px] text-cream-muted" role="status">{t('t.rl.unavailable')}</p>;
  }
  if (check.state !== 'done') return null;
  if (!shown.length && !hidden.length) {
    return (
      <p className="inline-flex items-center gap-1.5 text-[13px] text-cream-muted" role="status">
        <Icon name="check" className="h-3.5 w-3.5" />{t('t.rl.noConflict')}
      </p>
    );
  }
  return (
    <div className="space-y-2" role="status">
      {shown.map((c) => {
        const [a, b] = conflictNumbers(items, c);
        const reviewing = reviewed?.key === c.key;
        return (
          <div key={c.key} className="space-y-2 rounded-xl border border-gold/40 bg-gold-muted px-3 py-2.5">
            <p className="flex items-center gap-1.5 text-[14px] font-semibold text-gold">
              <Icon name="warning" className="h-4 w-4" />{t('t.rl.conflict', { a, b })}
            </p>
            {c.reason && <p className="text-[14px] text-cream-secondary">{c.reason}</p>}
            <p className="text-[12.5px] text-cream-muted">
              {t(reviewing ? 't.rl.reviewHelp' : 't.rl.conflictHelp')}
            </p>
            <div className="flex flex-wrap gap-2">
              {reviewing ? (
                <button type="button" onClick={onDone}
                  className="cursor-pointer rounded-lg px-3 py-1.5 text-[13px] text-cream-secondary hover:bg-ink-hover">{t('t.done')}</button>
              ) : (
                <button type="button" onClick={() => onReview(c)}
                  className="cursor-pointer rounded-lg border border-gold/40 px-3 py-1.5 text-[13px] text-gold hover:bg-gold-muted">{t('t.rl.review')}</button>
              )}
              <button type="button" onClick={() => onIgnore(c)}
                className="cursor-pointer rounded-lg px-3 py-1.5 text-[13px] text-cream-secondary hover:bg-ink-hover">{t('t.ignore')}</button>
            </div>
          </div>
        );
      })}
      {hidden.length > 0 && (
        <p className="text-[12.5px] text-cream-muted">
          {t('t.rl.ignoredCount', { n: hidden.length })} ·{' '}
          <button type="button" onClick={() => setShowIgnored(!showIgnored)}
            className="cursor-pointer text-cream underline underline-offset-2">{showIgnored ? t('t.hide') : t('t.show')}</button>
        </p>
      )}
      {showIgnored && hidden.map((c) => (
        <div key={c.key} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border-subtle px-3 py-2">
          <p className="text-[13px] text-cream-secondary">{t('t.rl.ignoredRules', { nums: conflictNumbers(items, c).join(' · ') })} {c.reason}</p>
          <button type="button" onClick={() => onWarnAgain(c)}
            className="cursor-pointer rounded-lg px-2 py-1 text-[13px] text-cream-secondary hover:bg-ink-hover hover:text-cream">{t('t.rl.warnAgain')}</button>
        </div>
      ))}
    </div>
  );
}

function Group({ title, note, children }) {
  return (
    <div>
      <div className="mb-1.5 flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-[13px] font-medium text-cream">{title}</p>
        <span className="text-[12px] text-cream-muted">{note}</span>
      </div>
      <div className="flex flex-wrap gap-1.5">{children}</div>
    </div>
  );
}

function RuleButton({ b, found, joined = false, disabled, onClick }) {
  return (
    <button type="button" title={b.text} aria-pressed={!!found} disabled={disabled} onClick={onClick}
      className={`cursor-pointer border px-2.5 py-1 text-[13px] transition-colors disabled:cursor-default disabled:opacity-50 ${
        joined ? 'rounded-l-full' : 'rounded-full'} ${found
        ? `border-ink bg-ink text-on-ink ${found.is_active ? '' : 'opacity-60'}`
        : 'border-border-default text-cream-secondary hover:bg-ink-hover hover:text-cream'}`}>
      {found ? '✓ ' : '+ '}{b.name}
    </button>
  );
}

function IconBtn({ title, onClick, danger, children }) {
  return (
    <button type="button" title={title} aria-label={title} onClick={onClick}
      className={`cursor-pointer rounded-lg p-1.5 text-cream-muted transition-colors ${
        danger ? 'hover:bg-danger-muted hover:text-danger' : 'hover:bg-ink-hover hover:text-cream'}`}>
      {children}
    </button>
  );
}
