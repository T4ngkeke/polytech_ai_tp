/**
 * mermaidRepair.js — the most common mistake of model-written flowcharts:
 * a node label with parentheses or other special characters, not quoted
 * (A[fib(5)] instead of A["fib(5)"]). Mermaid cannot read it; quoting the
 * label is what the Mermaid docs ask for. Used only when the source as
 * written does not parse (components/Diagram.jsx).
 */
const SPECIAL = /[()[\]{}<>|;:#&]/;

function quote(label) {
  return `"${label.replace(/"/g, '#quot;')}"`;
}

/** The flowchart with each unquoted [label] or {label} holding special characters quoted; other diagrams as they are. */
export function repairMermaid(source) {
  const first = (source.split('\n').find((l) => l.trim() && !l.trim().startsWith('%%')) || '').trim();
  if (!/^(flowchart|graph)\b/.test(first)) return source;
  return source.split('\n').map((line) => line
    // id[label] where label has a parenthesis etc. and no quotes or nested brackets
    .replace(/(\b[\w-]+)\[(?!")((?:[^[\]"]|\([^()"]*\))+?)\](?!\])/g, (whole, id, label) =>
      (SPECIAL.test(label) ? `${id}[${quote(label)}]` : whole))
    // id{label} (a decision)
    .replace(/(\b[\w-]+)\{(?!")((?:[^{}"])+?)\}(?!\})/g, (whole, id, label) =>
      (SPECIAL.test(label) ? `${id}{${quote(label)}}` : whole)),
  ).join('\n');
}
