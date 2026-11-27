// The plugins with which the document view draws a table or a formula block
// (components/BlockContent.jsx). Kept here, in plain JavaScript, so the tests
// run the same list.
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize from 'rehype-sanitize';
import rehypeKatex from 'rehype-katex';
import { rehypeCellMath } from './cellMath.js';
import { displayMathOnOwnLines } from './displayMath.js';

const KATEX = [rehypeKatex, { throwOnError: false }];

// Order matters: parse the HTML, filter it (GitHub's rules: table elements,
// rowspan, colspan; no style, class, script or event attribute), then mark and
// draw the formulas in the cells.
export const TABLE_PLUGINS = { remark: [remarkGfm, remarkMath], rehype: [rehypeRaw, rehypeSanitize, rehypeCellMath, KATEX] };
export const FORMULA_PLUGINS = { remark: [remarkMath], rehype: [KATEX] };

export function blockMarkdown(kind, content) {
  if (kind === 'table') return content;
  return displayMathOnOwnLines(content.trimStart().startsWith('$') ? content : `$$\n${content}\n$$`);
}
