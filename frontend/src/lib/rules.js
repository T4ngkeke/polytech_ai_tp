/**
 * rules.js — small helpers of the teacher's rule list (components/RuleList.jsx).
 */

/** A short name for a saved rule, made from its text: its first four words. */
export function autoName(text) {
  const words = text.trim().replace(/[.!?]+$/, '').split(/\s+/);
  return words.length <= 4 ? words.join(' ') : `${words.slice(0, 4).join(' ')}…`;
}
