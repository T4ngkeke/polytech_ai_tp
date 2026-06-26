/**
 * MessageContent.jsx — [v7.2] markdown rendering for assistant messages.
 *
 * Renders GitHub-flavored markdown, syntax-highlighted fenced code blocks (with a
 * copy button), and KaTeX math. react-markdown re-parses the full accumulated text
 * on each render, so streaming is inherently safe: a half-streamed ``` fence just
 * renders as partial text until it closes — no manual HTML stitching.
 *
 * Perf (react-best-practices):
 *  - components are defined at module level (rerender-no-inline-components),
 *  - MessageContent is memoized so streaming one message doesn't re-render others,
 *  - the highlighter is the async-light Prism build imported by a static path
 *    (bundle-analyzable-paths) so languages code-split on demand.
 */

import { memo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import SyntaxHighlighter from 'react-syntax-highlighter/dist/esm/prism-async';
import oneDark from 'react-syntax-highlighter/dist/esm/styles/prism/one-dark';
import 'katex/dist/katex.min.css';

const REMARK_PLUGINS = [remarkGfm, remarkMath];
const REHYPE_PLUGINS = [rehypeKatex];

function CopyButton({ value }) {
  const [copied, setCopied] = useState(false);
  const onCopy = () => {
    navigator.clipboard?.writeText(value).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <button
      onClick={onCopy}
      className="absolute top-2 right-2 px-2 py-0.5 rounded-md bg-ink-surface/80 border border-border-default text-[10px] text-cream-muted hover:text-cream cursor-pointer"
    >
      {copied ? 'Copied' : 'Copy'}
    </button>
  );
}

function CodeBlock({ language, value }) {
  return (
    <div className="relative my-2 text-[13px]">
      <CopyButton value={value} />
      <SyntaxHighlighter
        language={language}
        style={oneDark}
        customStyle={{ margin: 0, borderRadius: '0.5rem', padding: '0.9rem 1rem' }}
        PreTag="div"
      >
        {value}
      </SyntaxHighlighter>
    </div>
  );
}

const MARKDOWN_COMPONENTS = {
  // CodeBlock renders its own block wrapper, so collapse the default <pre>.
  pre: ({ children }) => <>{children}</>,
  code({ className, children }) {
    const match = /language-(\w+)/.exec(className || '');
    const text = String(children).replace(/\n$/, '');
    const isBlock = Boolean(match) || text.includes('\n');
    return isBlock ? (
      <CodeBlock language={match ? match[1] : 'text'} value={text} />
    ) : (
      <code className="rounded bg-ink-surface px-1 py-0.5 text-[0.85em] text-cyan">{children}</code>
    );
  },
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noreferrer" className="text-cyan underline">{children}</a>
  ),
};

function MessageContent({ content }) {
  return (
    <div className="prose-chat space-y-2 break-words">
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        rehypePlugins={REHYPE_PLUGINS}
        components={MARKDOWN_COMPONENTS}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

export default memo(MessageContent);
