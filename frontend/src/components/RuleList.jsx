/**
 * RuleList.jsx — the rules a teacher gives the tutor, one per line.
 *
 * For a class, a lab, or a student in one class (classId). Each rule can be
 * edited, switched off or deleted; ready-made rules and the teacher's own
 * presets are added with one click. The active rules go into the prompt as a
 * numbered list (backend rule_service).
 *
 * Data: GET/POST /api/teacher/rule-items, PATCH/DELETE /api/teacher/rule-items/{id},
 * GET /api/teacher/rule-presets (ready-made), /api/teacher/skill-presets (own).
 */
import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';

import api from '../lib/api';
import Icon from './Icon';

const MAX_RULES = 30;

export default function RuleList({ level, targetId, classId, inherited, inheritedLabel, compact = false }) {
  const [items, setItems] = useState(null);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(null);  // { id, text }

  const query = `level=${level}&target_id=${targetId}${level === 'student' ? `&class_id=${classId}` : ''}`;
  const load = useCallback(async () => {
    try { setItems(await api.get(`/api/teacher/rule-items?${query}`)); }
    catch (err) { setItems([]); toast.error(err.message); }
  }, [query]);
  useEffect(() => { setItems(null); load(); }, [load]);

  const add = async (text) => {
    const clean = text.trim();
    if (!clean) return false;
    if (items?.some((r) => r.text.trim() === clean)) {
      toast('This rule is already in the list');
      return false;
    }
    setBusy(true);
    try {
      const created = await api.post('/api/teacher/rule-items', {
        level, target_id: targetId, ...(level === 'student' ? { class_id: classId } : {}), text: clean });
      setItems((prev) => [...(prev || []), created]);
      return true;
    } catch (err) {
      toast.error(err.message === 'too_many_rules' ? `At most ${MAX_RULES} rules` : err.message);
      return false;
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

  const remove = async (item) => {
    if (!window.confirm(`Delete this rule?\n\n${item.text}`)) return;
    try {
      await api.delete(`/api/teacher/rule-items/${item.id}`);
      setItems((prev) => prev.filter((r) => r.id !== item.id));
    } catch (err) { toast.error(err.message); }
  };

  const saveAsPreset = async (item) => {
    const name = item.text.length > 40 ? `${item.text.slice(0, 40).trim()}…` : item.text;
    try {
      await api.post('/api/teacher/skill-presets', { name, content: item.text });
      toast.success('Saved in My rules');
      window.dispatchEvent(new Event('edullm-presets-changed'));
    } catch (err) { toast.error(err.message); }
  };

  const submitDraft = async () => {
    if (await add(draft)) setDraft('');
  };

  if (items === null) return <p className="text-xs text-cream-muted">Loading rules…</p>;
  const texts = new Set(items.map((r) => r.text.trim()));
  const active = items.filter((r) => r.is_active).length;
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
        {items.length > 0 && (
          <ol className="space-y-1.5">
            {items.map((r, i) => (
              <li key={r.id} className={`group flex items-start gap-3 rounded-xl border px-3 py-2 ${
                r.is_active ? 'border-border-default bg-ink-raised' : 'border-border-subtle bg-ink-base'}`}>
                <span className="mt-0.5 w-5 shrink-0 text-right font-mono text-xs text-cream-muted">{i + 1}.</span>
                {editing?.id === r.id ? (
                  <div className="flex-1 space-y-2">
                    <textarea value={editing.text} autoFocus rows={2} aria-label="Rule text"
                      onChange={(e) => setEditing({ ...editing, text: e.target.value })}
                      onKeyDown={async (e) => {
                        if (e.key === 'Escape') setEditing(null);
                        if (e.key === 'Enter' && !e.shiftKey) {
                          e.preventDefault();
                          if (editing.text.trim() && await patch(r, { text: editing.text })) setEditing(null);
                        }
                      }}
                      className="w-full resize-y rounded-lg border border-border-default bg-ink-deep px-3 py-2 text-sm text-cream focus:border-cyan/40 focus:outline-none" />
                    <div className="flex gap-2">
                      <button type="button" disabled={!editing.text.trim()}
                        onClick={async () => { if (await patch(r, { text: editing.text })) setEditing(null); }}
                        className="cursor-pointer rounded-lg bg-cyan-muted px-3 py-1 text-xs text-cyan hover:bg-cyan/20 disabled:opacity-40">Save</button>
                      <button type="button" onClick={() => setEditing(null)}
                        className="cursor-pointer rounded-lg px-3 py-1 text-xs text-cream-secondary hover:bg-ink-hover">Cancel</button>
                    </div>
                  </div>
                ) : (
                  <p className={`flex-1 whitespace-pre-wrap text-sm ${r.is_active ? 'text-cream' : 'text-cream-muted line-through'}`}>{r.text}</p>
                )}
                {editing?.id !== r.id && (
                  <div className="flex shrink-0 items-center gap-0.5">
                    <Switch on={r.is_active} label={r.is_active ? 'Switch this rule off' : 'Switch this rule on'}
                      onToggle={() => patch(r, { is_active: !r.is_active })} />
                    <IconBtn title="Edit" onClick={() => setEditing({ id: r.id, text: r.text })}><Icon name="pencil" className="h-3.5 w-3.5" /></IconBtn>
                    {!compact && <IconBtn title="Save in My rules (to reuse in other classes)" onClick={() => saveAsPreset(r)}><Icon name="copy" className="h-3.5 w-3.5" /></IconBtn>}
                    <IconBtn title="Delete" danger onClick={() => remove(r)}><Icon name="trash" className="h-3.5 w-3.5" /></IconBtn>
                  </div>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>

      <div className="flex items-end gap-2">
        <textarea value={draft} rows={1} placeholder="Write a new rule… (Enter to add)" aria-label="New rule"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitDraft(); } }}
          className="min-h-[2.5rem] flex-1 resize-y rounded-xl border border-border-default bg-ink-deep px-3 py-2 text-sm text-cream placeholder:text-cream-muted focus:border-cyan/40 focus:outline-none" />
        <button type="button" onClick={submitDraft} disabled={busy || !draft.trim() || items.length >= MAX_RULES}
          className="cursor-pointer rounded-xl gradient-cyan px-4 py-2 text-sm font-semibold text-on-accent hover:brightness-110 disabled:opacity-40">
          Add
        </button>
      </div>

      <Presets texts={texts} onPick={add} disabled={busy || items.length >= MAX_RULES} compact={compact} />
    </div>
  );
}

function Presets({ texts, onPick, disabled, compact }) {
  const [builtIn, setBuiltIn] = useState([]);
  const [mine, setMine] = useState([]);
  const loadMine = useCallback(() => {
    api.get('/api/teacher/skill-presets').then(setMine).catch(() => {});
  }, []);
  useEffect(() => {
    api.get('/api/teacher/rule-presets').then(setBuiltIn).catch(() => {});
    loadMine();
    window.addEventListener('edullm-presets-changed', loadMine);
    return () => window.removeEventListener('edullm-presets-changed', loadMine);
  }, [loadMine]);

  const removeMine = async (p) => {
    try {
      await api.delete(`/api/teacher/skill-presets/${p.id}`);
      setMine((prev) => prev.filter((x) => x.id !== p.id));
    } catch (err) { toast.error(err.message); }
  };

  return (
    <div className="rounded-xl border border-border-subtle bg-ink-raised/40 p-3 space-y-3">
      <PresetGroup title="Ready-made rules" note="Click to add"
        items={builtIn.map((p) => ({ key: p.name, name: p.name, text: p.text }))}
        texts={texts} onPick={onPick} disabled={disabled} />
      {!compact && (
        <PresetGroup title="My rules" note={mine.length ? 'Click to add' : 'Save a rule here with its copy button to reuse it in other classes'}
          items={mine.map((p) => ({ key: p.id, name: p.name, text: p.content, onRemove: () => removeMine(p) }))}
          texts={texts} onPick={onPick} disabled={disabled} />
      )}
    </div>
  );
}

function PresetGroup({ title, note, items, texts, onPick, disabled }) {
  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between gap-2">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-cyan/80">{title}</p>
        <span className="text-[11px] text-cream-muted">{note}</span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {items.map((p) => {
          const added = texts.has(p.text.trim());
          return (
            <span key={p.key} className="inline-flex items-center">
              <button type="button" title={p.text} disabled={disabled || added} onClick={() => onPick(p.text)}
                className={`cursor-pointer rounded-full border px-2.5 py-1 text-xs transition-colors disabled:cursor-default ${
                  added ? 'border-cyan/30 bg-cyan-muted text-cyan' : 'border-border-default text-cream-secondary hover:border-cyan/40 hover:text-cream disabled:opacity-50'}`}>
                {added ? '✓ ' : '+ '}{p.name}
              </button>
              {p.onRemove && (
                <button type="button" title="Remove from My rules" onClick={p.onRemove}
                  className="ml-0.5 cursor-pointer rounded-full p-1 text-cream-muted hover:text-danger">
                  <Icon name="close" className="h-3 w-3" />
                </button>
              )}
            </span>
          );
        })}
      </div>
    </div>
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
