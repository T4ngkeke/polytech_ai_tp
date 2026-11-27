/**
 * BlockContent.jsx — how the document view draws a table or a formula block.
 *
 * table: MinerU gives a table as HTML. rehype-raw parses it and rehype-sanitize
 *   keeps only what GitHub keeps (table elements, rowspan and colspan; no
 *   style, class, script or event attribute), then the formulas in the cells
 *   are drawn by KaTeX (lib/cellMath.js). A table written in Markdown is drawn
 *   too (remark-gfm).
 * formula: the LaTeX (`$$ … $$`) drawn by KaTeX.
 * The source stays what the teacher edits; this is only the display. Answers
 * in the chat never go through here: they may not hold HTML.
 */
import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import { blockMarkdown, FORMULA_PLUGINS, TABLE_PLUGINS } from '../lib/blockPlugins.js';
import 'katex/dist/katex.min.css';

function BlockContent({ kind, content }) {
  const plugins = kind === 'table' ? TABLE_PLUGINS : FORMULA_PLUGINS;
  return (
    <div className={`prose-chat prose-small ${kind === 'table' ? 'prose-table' : ''}`}>
      <ReactMarkdown remarkPlugins={plugins.remark} rehypePlugins={plugins.rehype}>{blockMarkdown(kind, content)}</ReactMarkdown>
    </div>
  );
}

export default memo(BlockContent);
