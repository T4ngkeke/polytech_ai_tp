// A formula the model writes as `$$ … $$` on one line is, for remark-math 6,
// a formula inside a sentence, and KaTeX draws `\tag{11}` there as red source.
// The same formula with `$$` on their own lines is a display formula. So a
// line that holds only `$$ … $$` is split into three lines, with blank lines
// around it. Code blocks are left as they are.

const ONE_LINE = /^(\s*)\$\$(?!\$)(.+?)\$\$\s*$/;
const FENCE = /^\s*(```|~~~)/;

export function displayMathOnOwnLines(text) {
  if (!text || !text.includes('$$')) return text;
  const out = [];
  let fence = null;
  for (const line of text.split('\n')) {
    const opened = line.match(FENCE);
    if (opened) {
      if (fence === null) fence = opened[1];
      else if (opened[1] === fence) fence = null;
      out.push(line);
      continue;
    }
    const math = fence === null ? line.match(ONE_LINE) : null;
    if (!math || math[2].includes('$$') || !math[2].trim()) {
      out.push(line);
      continue;
    }
    const [, indent, body] = math;
    if (out.length && out[out.length - 1].trim() !== '') out.push('');
    out.push(`${indent}$$`, `${indent}${body.trim()}`, `${indent}$$`, '');
  }
  return out.join('\n');
}
