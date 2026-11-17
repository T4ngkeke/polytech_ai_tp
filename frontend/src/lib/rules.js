/**
 * rules.js — small helpers of the teacher's rule list (components/RuleList.jsx).
 */

/** A short name for a saved rule, made from its text: its first four words. */
export function autoName(text) {
  const words = text.trim().replace(/[.!?]+$/, '').split(/\s+/);
  return words.length <= 4 ? words.join(' ') : `${words.slice(0, 4).join(' ')}…`;
}

/*
 * The contradiction check (POST /api/teacher/rule-items/check). A conflict is
 * { rule_ids: [a, b], reason }. An ignored conflict is remembered in this
 * browser by the two rule texts, so an edited rule is checked again.
 */

/** The key of a conflict: its two rule texts, in a fixed order. */
export function conflictKey(items, conflict) {
  const texts = conflict.rule_ids.map((id) => items.find((r) => r.id === id)?.text ?? '');
  return texts.sort().join('\u0000');
}

/** Conflicts whose two rules are still in the list and switched on. */
export function liveConflicts(conflicts, items) {
  return conflicts.filter((c) => c.rule_ids.every((id) => items.some((r) => r.id === id && r.is_active)));
}

/** The page numbers (1-based) of a conflict's two rules. */
export function conflictNumbers(items, conflict) {
  return conflict.rule_ids.map((id) => items.findIndex((r) => r.id === id) + 1).sort((a, b) => a - b);
}

const IGNORED_PREFIX = 'edullm-rule-ignored:';

/** The ignored conflict keys of one list (empty when storage is unavailable). */
export function loadIgnored(listKey, storage = globalThis.localStorage) {
  try {
    const value = JSON.parse(storage?.getItem(IGNORED_PREFIX + listKey) ?? '[]');
    return Array.isArray(value) ? value.filter((k) => typeof k === 'string') : [];
  } catch {
    return [];
  }
}

export function saveIgnored(listKey, keys, storage = globalThis.localStorage) {
  try { storage?.setItem(IGNORED_PREFIX + listKey, JSON.stringify(keys)); } catch { /* private window */ }
}
