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
 * Data: GET/POST /api/teacher/rule-items, PATCH/DELETE /api/teacher/rule-items/{id},
 * GET /api/teacher/rule-presets (ready-made), /api/teacher/skill-presets (My rules).
 * The interaction was agreed on a prototype first (2026-11-10).
 */
import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';

import api from '../lib/api';
import { autoName } from '../lib/rules';
import Icon from './Icon';

const MAX_RULES = 30;
const ONLY_HERE = { class: 'This class only', lab: 'This TP only', student: 'This student only' };

function undoToast(message, undo) {
  toast((t) => (
    <span className="flex items-center gap-3">
      <span>{message}</span>
      {undo && (
        <button type="button" className="cursor-pointer font-semibold text-cyan hover:underline"
          onClick={() => { toast.dismiss(t.id); undo(); }}>
          Undo
        </button>
      )}
    </span>
  ), { duration: 6000 });
}

const short = (text) => (text.length > 40 ? `${text.slice(0, 40)}…` : text);

export default function RuleList({ level, targetId, classId, inherited, inheritedLabel }) {
  const [items, setItems] = useState(null);
  const [builtIn, setBuiltIn] = useState([]);
  const [mine, setMine] = useState([]);
  const [busy, setBusy] = useState(false);
  const [editingRule, setEditingRule] = useState(null);  // { id, text }
  const [editor, setEditor] = useState(null);            // { mode: 'new'|'edit', id?, text, name, save }

  const query = `level=${level}&target_id=${targetId}${level === 'student' ? `&class_id=${classId}` : ''}`;
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
    ...builtIn.map((p) => ({ source: `b:${p.key}`, name: p.name, text: p.text, group: 'Ready-made' })),
    ...mine.map((p) => ({ source: `m:${p.id}`, name: p.name, text: p.content, group: 'My rules', preset: p })),
  ];
  const origin = (source) => {
    const b = buttons.find((x) => x.source === source);
    return b ? `${b.group} · ${b.name}` : source ? 'My rules' : ONLY_HERE[level];
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
      return created;
    } catch (err) {
      toast.error(err.message === 'too_many_rules' ? `At most ${MAX_RULES} rules` : err.message);
      return null;
    } finally {
      setBusy(false);
    }
  };

  const patch = async (item, body) => {
    try {
      const updated = await api.patch(`/api/teacher/rule-items/${item.id}`, body);
      setItems((prev) => prev.map((r) => (r.id === item.id ? updated : r)));
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
      setItems((prev) => prev.filter((r) => r.id !== item.id));
      undoToast(`Removed “${short(item.text)}”`, () => add(item.text, item.source,
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
        toast.success(`Saved “${name}”`);
        loadMine(); minesChanged(); await load();
      } else if (editor.save) {
        const preset = await api.post('/api/teacher/skill-presets', { name, content: text });
        loadMine(); minesChanged();
        if (await add(text, `m:${preset.id}`)) toast.success('Added, and saved in My rules');
      } else if (await add(text, null)) {
        toast.success(`Added (${ONLY_HERE[level].toLowerCase()})`);
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
      undoToast(`“${preset.name}” removed from My rules`, async () => {
        const again = await api.post('/api/teacher/skill-presets', { name: preset.name, content: preset.content });
        await Promise.all(linked.map((r) => api.patch(`/api/teacher/rule-items/${r.id}`, { source: `m:${again.id}` })));
        loadMine(); minesChanged(); load();
      });
    } catch (err) { toast.error(err.message); }
  };

  const saveRule = async (item) => {
    if (editingRule.text.trim() && await patch(item, { text: editingRule.text.trim() })) setEditingRule(null);
  };

  if (items === null) return <p className="text-xs text-cream-muted">Loading rules…</p>;
  const active = items.filter((r) => r.is_active).length;
  const full = items.length >= MAX_RULES;
  return (
    <div className="space-y-4">
      {inherited?.length > 0 && (
        <div>
          <p className="text-[10px] text-cream-muted uppercase tracking-widest mb-2 font-semibold">{inheritedLabel}</p>
          <ol className="space-y-1">
            {inherited.map((r, i) => (
              <li key={r.id} className="flex gap-2 rounded-lg bg-ink-raised/60 px-3 py-2 text-xs text-cream-secondary">
                <span className="font-mono text-cream-muted">{i + 1}.</span><span className="whitespace-pre-wrap">{r.text}</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      <div>
        <div className="mb-2 flex items-baseline justify-between">
          <p className="text-[10px] text-cream-muted uppercase tracking-widest font-semibold">Rules</p>
          <span className="text-[11px] text-cream-muted">
            {items.length ? `${active} on · ${items.length} of ${MAX_RULES}` : 'No rule yet'}
          </span>
        </div>
        {items.length === 0 ? (
          <p className="text-xs italic text-cream-muted">No rule yet. Pick one below, or write your own.</p>
        ) : (
          <ol className="space-y-1.5">
            {items.map((r, i) => (
              <li key={r.id} className={`flex items-start gap-3 rounded-xl border px-3 py-2 ${
                r.is_active ? 'border-border-default bg-ink-raised' : 'border-border-subtle bg-ink-base'}`}>
                <span className="mt-0.5 w-5 shrink-0 text-right font-mono text-xs text-cream-muted">{i + 1}.</span>
                {editingRule?.id === r.id ? (
                  <div className="flex-1 space-y-2">
                    <textarea value={editingRule.text} autoFocus rows={2} aria-label="Rule text"
                      onChange={(e) => setEditingRule({ ...editingRule, text: e.target.value })}
                      onKeyDown={(e) => {
                        if (e.key === 'Escape') setEditingRule(null);
                        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveRule(r); }
                      }}
                      className="w-full resize-y rounded-lg border border-border-default bg-ink-deep px-3 py-2 text-sm text-cream focus:border-cyan/40 focus:outline-none" />
                    <div className="flex gap-2">
                      <button type="button" disabled={!editingRule.text.trim()} onClick={() => saveRule(r)}
                        className="cursor-pointer rounded-lg bg-cyan-muted px-3 py-1 text-xs text-cyan hover:bg-cyan/20 disabled:opacity-40">Save</button>
                      <button type="button" onClick={() => setEditingRule(null)}
                        className="cursor-pointer rounded-lg px-3 py-1 text-xs text-cream-secondary hover:bg-ink-hover">Cancel</button>
                    </div>
                  </div>
                ) : (
                  <div className="min-w-0 flex-1">
                    <p className={`whitespace-pre-wrap text-sm ${r.is_active ? 'text-cream' : 'text-cream-muted line-through'}`}>{r.text}</p>
                    <p className="mt-0.5 text-[11px] text-cream-muted">{origin(r.source)}</p>
                  </div>
                )}
                {editingRule?.id !== r.id && (
                  <div className="flex shrink-0 items-center gap-0.5">
                    <Switch on={r.is_active} label={r.is_active ? 'Switch this rule off (it stays in the list)' : 'Switch this rule on'}
                      onToggle={() => patch(r, { is_active: !r.is_active })} />
                    <IconBtn title="Edit" onClick={() => setEditingRule({ id: r.id, text: r.text })}><Icon name="pencil" className="h-3.5 w-3.5" /></IconBtn>
                    <IconBtn title="Delete" danger onClick={() => remove(r)}><Icon name="trash" className="h-3.5 w-3.5" /></IconBtn>
                  </div>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>

      <div className="rounded-xl border border-border-subtle bg-ink-raised/40 p-3 space-y-3">
        <Group title="Ready-made rules" note="Click to add, click again to remove">
          {buttons.filter((b) => !b.preset).map((b) => (
            <RuleButton key={b.source} b={b} found={inList(b.source)} disabled={busy || (full && !inList(b.source))}
              onClick={() => toggle(b)} />
          ))}
        </Group>
        <Group title="My rules" note="Saved by you, for all your classes">
          {buttons.filter((b) => b.preset).map((b) => (
            <span key={b.source} className="inline-flex items-stretch">
              <RuleButton b={b} found={inList(b.source)} joined disabled={busy || (full && !inList(b.source))}
                onClick={() => toggle(b)} />
              <button type="button" title="Edit this saved rule" aria-label={`Edit ${b.name}`}
                onClick={() => setEditor({ mode: 'edit', id: b.preset.id, text: b.preset.content, name: b.preset.name, save: true })}
                className={`cursor-pointer rounded-r-full border border-l-0 px-2 transition-colors ${inList(b.source)
                  ? 'border-cyan/30 bg-cyan-muted text-cyan' : 'border-border-default text-cream-muted hover:text-cream'}`}>
                <Icon name="pencil" className="h-3 w-3" />
              </button>
            </span>
          ))}
          <button type="button" disabled={full} onClick={() => setEditor({ mode: 'new', text: '', name: '', save: true })}
            className="cursor-pointer rounded-full border border-dashed border-cyan/50 px-2.5 py-1 text-xs text-cyan hover:bg-cyan-muted disabled:opacity-40">
            + Custom rule
          </button>
        </Group>

        {editor && (
          <div className="space-y-2 rounded-xl border border-cyan/30 bg-ink-base p-3">
            <label className="block text-xs text-cream-secondary" htmlFor="rule-editor-text">
              {editor.mode === 'edit' ? 'Edit your saved rule' : 'Your rule'}
            </label>
            <textarea id="rule-editor-text" rows={3} autoFocus value={editor.text}
              placeholder="e.g. Answer with an example from the TP subject first."
              onChange={(e) => setEditor({ ...editor, text: e.target.value })}
              onKeyDown={(e) => { if (e.key === 'Escape') setEditor(null); }}
              className="w-full resize-y rounded-lg border border-border-default bg-ink-deep px-3 py-2 text-sm text-cream placeholder:text-cream-muted focus:border-cyan/40 focus:outline-none" />
            {(editor.save || editor.mode === 'edit') && (
              <>
                <label className="block text-xs text-cream-secondary" htmlFor="rule-editor-name">Short name (shown on the button)</label>
                <input id="rule-editor-name" type="text" value={editor.name}
                  placeholder={editor.text.trim() ? autoName(editor.text) : 'Made from the rule if empty'}
                  onChange={(e) => setEditor({ ...editor, name: e.target.value })}
                  className="w-full rounded-lg border border-border-default bg-ink-deep px-3 py-1.5 text-sm text-cream placeholder:text-cream-muted focus:border-cyan/40 focus:outline-none" />
              </>
            )}
            {editor.mode === 'new' && (
              <label className="flex cursor-pointer items-center gap-2 text-xs text-cream-secondary" htmlFor="rule-editor-save">
                <input id="rule-editor-save" type="checkbox" checked={editor.save}
                  onChange={(e) => setEditor({ ...editor, save: e.target.checked })} className="h-4 w-4 accent-[rgb(var(--cyan))]" />
                Also save in My rules, to reuse in other classes
              </label>
            )}
            {editor.mode === 'edit' && (
              <p className="text-[11px] text-cream-muted">Rules added from it change too, unless you edited them in a list.</p>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" disabled={busy || !editor.text.trim()} onClick={saveEditor}
                className="cursor-pointer rounded-lg gradient-cyan px-3 py-1.5 text-xs font-semibold text-on-accent hover:brightness-110 disabled:opacity-40">
                {editor.mode === 'edit' ? 'Save' : 'Add rule'}
              </button>
              <button type="button" onClick={() => setEditor(null)}
                className="cursor-pointer rounded-lg px-3 py-1.5 text-xs text-cream-secondary hover:bg-ink-hover">Cancel</button>
              {editor.mode === 'edit' && (
                <button type="button" onClick={() => deleteMine(mine.find((m) => m.id === editor.id))}
                  className="ml-auto cursor-pointer rounded-lg px-3 py-1.5 text-xs text-danger hover:bg-danger-muted">
                  Delete from My rules
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Group({ title, note, children }) {
  return (
    <div>
      <div className="mb-1.5 flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-cyan/80">{title}</p>
        <span className="text-[11px] text-cream-muted">{note}</span>
      </div>
      <div className="flex flex-wrap gap-1.5">{children}</div>
    </div>
  );
}

function RuleButton({ b, found, joined = false, disabled, onClick }) {
  return (
    <button type="button" title={b.text} aria-pressed={!!found} disabled={disabled} onClick={onClick}
      className={`cursor-pointer border px-2.5 py-1 text-xs transition-colors disabled:cursor-default disabled:opacity-50 ${
        joined ? 'rounded-l-full' : 'rounded-full'} ${found
        ? `border-cyan/30 bg-cyan-muted text-cyan ${found.is_active ? '' : 'opacity-60'}`
        : 'border-border-default text-cream-secondary hover:border-cyan/40 hover:text-cream'}`}>
      {found ? '✓ ' : '+ '}{b.name}
    </button>
  );
}

function Switch({ on, onToggle, label }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} title={label} onClick={onToggle}
      className={`relative mx-1 h-5 w-9 shrink-0 cursor-pointer rounded-full transition-colors ${on ? 'bg-cyan' : 'bg-ink-surface border border-border-default'}`}>
      <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-cream shadow transition-all ${on ? 'left-4' : 'left-0.5'}`} />
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
