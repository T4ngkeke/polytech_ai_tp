/**
 * MessageContent.jsx — [v7.2] markdown rendering for assistant messages.
 *
 * Renders GitHub-flavored markdown, syntax-highlighted fenced code blocks (with a
 * copy button), and KaTeX math. react-markdown re-parses the full accumulated text
 * on each render, so streaming is inherently safe for *text*: a half-streamed ```
 * fence just renders as partial text until it closes — no manual HTML stitching.
 *
 * Streaming + syntax highlighting (v7.2 crash fix):
 *  - While a message is still streaming we do NOT run Prism. An unclosed ``` fence
 *    makes the entire growing tail one code block, and re-highlighting that from
 *    scratch on every token builds huge React trees → the tab runs out of memory
 *    and crashes (worst in Safari / fullscreen). Mid-stream we render code as a
 *    plain <pre>; once the stream completes we highlight the finished block once.
 *  - KaTeX runs with throwOnError:false so a half-typed `$…$` during streaming
 *    renders as text instead of throwing and blanking the message.
 *
 * Perf (react-best-practices):
 *  - components are defined at module level (rerender-no-inline-components),
 *  - MessageContent is memoized so streaming one message doesn't re-render others,
 *  - the highlighter is the async Prism build imported by a static path
 *    (bundle-analyzable-paths) so languages code-split on demand.
 */

import { memo, useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import SyntaxHighlighter from 'react-syntax-highlighter/dist/esm/prism-async';
import oneDark from 'react-syntax-highlighter/dist/esm/styles/prism/one-dark';
import 'katex/dist/katex.min.css';

const REMARK_PLUGINS = [remarkGfm, remarkMath];
// throwOnError:false — a partial `$…$` mid-stream must not throw and blank the bubble.
const REHYPE_PLUGINS = [[rehypeKatex, { throwOnError: false }]];

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

const CodeBlock = memo(function CodeBlock({ language, value }) {
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
});

// Mid-stream fallback: a plain, un-highlighted block. Cheap to render on every
// token, so a growing/unclosed fence can't blow up memory.
function PlainCodeBlock({ value }) {
  return (
    <div className="relative my-2 text-[13px]">
      <CopyButton value={value} />
      <pre className="overflow-x-auto rounded-lg border border-border-subtle bg-ink-deep p-3.5 font-mono text-[13px] leading-relaxed text-cream-secondary">
        <code>{value}</code>
      </pre>
    </div>
  );
}

// The components map depends on `streaming`, so it is built per-message via
// useMemo (the inner block components are module-level + stable).
function makeComponents(streaming) {
  return {
    // CodeBlock renders its own block wrapper, so collapse the default <pre>.
    pre: ({ children }) => <>{children}</>,
    code({ className, children }) {
      const match = /language-(\w+)/.exec(className || '');
      const text = String(children).replace(/\n$/, '');
      const isBlock = Boolean(match) || text.includes('\n');
      if (!isBlock) {
        return <code className="rounded bg-ink-surface px-1 py-0.5 text-[0.85em] text-cyan">{children}</code>;
      }
      return streaming
        ? <PlainCodeBlock value={text} />
        : <CodeBlock language={match ? match[1] : 'text'} value={text} />;
    },
    a: ({ children, href }) => (
      <a href={href} target="_blank" rel="noreferrer" className="text-cyan underline">{children}</a>
    ),
  };
}

function MessageContent({ content, streaming = false }) {
  const components = useMemo(() => makeComponents(streaming), [streaming]);
  return (
    <div className="prose-chat space-y-2 break-words">
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        rehypePlugins={REHYPE_PLUGINS}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

export default memo(MessageContent);
