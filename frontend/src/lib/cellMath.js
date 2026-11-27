// MinerU writes formulas inside table cells as `$…$` (and sometimes `$$…$$`)
// in the cell's text. remark-math only reads Markdown, never the text inside
// HTML, so these stay as source. This rehype plugin, run after the HTML is
// parsed and filtered (rehype-raw, rehype-sanitize), marks them the way
// remark-math does (`math-inline`, `math-display`), and rehype-katex draws them.
// It changes the tree, not the page: React keeps the page.

const MATH = /\$\$([\s\S]+?)\$\$|\$((?:\\.|[^$\\\n])+?)\$/g;
const CELLS = new Set(['td', 'th']);

function mathNode(latex, display) {
  return {
    type: 'element',
    tagName: 'span',
    properties: { className: [display ? 'math-display' : 'math-inline'] },
    children: [{ type: 'text', value: latex }],
  };
}

export function splitMath(value) {
  const parts = [];
  let last = 0;
  for (const match of value.matchAll(MATH)) {
    const latex = (match[1] ?? match[2]).trim();
    if (!latex) continue;
    if (match.index > last) parts.push({ type: 'text', value: value.slice(last, match.index) });
    parts.push(mathNode(latex, match[1] !== undefined));
    last = match.index + match[0].length;
  }
  if (!parts.length) return null;
  if (last < value.length) parts.push({ type: 'text', value: value.slice(last) });
  return parts;
}

function walk(node, inCell) {
  if (!node.children) return;
  const cell = inCell || (node.type === 'element' && CELLS.has(node.tagName));
  const children = [];
  for (const child of node.children) {
    const parts = cell && child.type === 'text' ? splitMath(child.value) : null;
    if (parts) children.push(...parts);
    else {
      walk(child, cell);
      children.push(child);
    }
  }
  node.children = children;
}

export function rehypeCellMath() {
  return (tree) => walk(tree, false);
}
