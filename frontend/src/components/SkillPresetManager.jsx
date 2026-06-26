/**
 * SkillPresetManager.jsx — [v7.2] teacher skill-preset library.
 *
 * A teacher keeps a private library of reusable instructor-style ("skill")
 * presets. "Use" loads a preset into the class rule editor (snapshot at save
 * time); "Save current as preset" stores the editor text for reuse.
 */

import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';

import api from '../lib/api';

export default function SkillPresetManager({ currentText, onUse }) {
  const [presets, setPresets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [newName, setNewName] = useState('');
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try { setPresets(await api.get('/api/teacher/skill-presets')); }
    catch { /* non-fatal */ } finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleSaveAsPreset = async () => {
    const name = newName.trim();
    if (!name || !currentText.trim()) return;
    setSaving(true);
    try {
      const created = await api.post('/api/teacher/skill-presets', { name, content: currentText });
      setPresets((p) => [...p, created]);
      setNewName('');
      toast.success('Preset saved');
    } catch (err) { toast.error(err.message); } finally { setSaving(false); }
  };

  const handleDelete = async (id) => {
    try {
      await api.delete(`/api/teacher/skill-presets/${id}`);
      setPresets((p) => p.filter((x) => x.id !== id));
    } catch (err) { toast.error(err.message); }
  };

  return (
    <div className="rounded-xl border border-border-default bg-ink-raised/40 p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-cyan/80">Skill presets</h4>
        <span className="text-[10px] text-cream-muted">{presets.length} saved</span>
      </div>

      {loading ? (
        <p className="text-xs text-cream-muted">Loading…</p>
      ) : presets.length === 0 ? (
        <p className="text-xs text-cream-muted">No presets yet — save the current style below to reuse it across classes.</p>
      ) : (
        <ul className="space-y-1.5">
          {presets.map((p) => (
            <li key={p.id} className="flex items-center gap-2">
              <span className="flex-1 min-w-0 truncate text-sm text-cream">{p.name}</span>
              <button onClick={() => onUse(p.content)}
                className="text-xs text-cyan hover:bg-cyan-muted px-2 py-1 rounded-lg cursor-pointer">Use</button>
              <button onClick={() => handleDelete(p.id)}
                className="text-xs text-danger hover:bg-danger-muted px-2 py-1 rounded-lg cursor-pointer">✕</button>
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-center gap-2 pt-1">
        <input value={newName} onChange={(e) => setNewName(e.target.value)}
          placeholder="Preset name"
          className="flex-1 px-3 py-2 rounded-lg bg-ink-deep border border-border-default text-cream text-xs placeholder:text-cream-muted focus:outline-none focus:border-cyan/40" />
        <button onClick={handleSaveAsPreset} disabled={!newName.trim() || !currentText.trim() || saving}
          className="px-3 py-2 rounded-lg bg-cyan-muted text-cyan border border-cyan/20 text-xs font-medium hover:bg-cyan/20 transition-all cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed">
          {saving ? 'Saving…' : 'Save current as preset'}
        </button>
      </div>
    </div>
  );
}
