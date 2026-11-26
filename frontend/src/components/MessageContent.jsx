/**
 * MessageContent.jsx — markdown rendering for assistant messages.
 *
 * Also: [E#] course citations as gray pills (onCite opens the passage;
 * citeInfo gives the hover card), [W#] web sources as gray pills (webSources
 * map), ```mermaid diagrams and ```chart charts (components/Diagram, Chart;
 * the only drawn formats, checked by the server), and a Run button on Python
 * code when onRun is given (lib/python.js, in this browser).
 *
 * Renders GitHub-flavored markdown, syntax-highlighted fenced code blocks (with a
 * copy button), and KaTeX math. react-markdown re-parses the full accumulated text
 * on each render, so streaming is inherently safe for *text*: a half-streamed ```
 * fence just renders as partial text until it closes — no manual HTML stitching.
 *
 * Streaming + syntax highlighting (crash fix):
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

import { memo, useMemo, useState, lazy, Suspense } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import SyntaxHighlighter from 'react-syntax-highlighter/dist/esm/prism-async';
import oneDark from 'react-syntax-highlighter/dist/esm/styles/prism/one-dark';
import { outputParts } from '../lib/python';
import { useT } from '../lib/i18n';
import 'katex/dist/katex.min.css';

const Diagram = lazy(() => import('./Diagram'));
const Chart = lazy(() => import('./Chart'));

const REMARK_PLUGINS = [remarkGfm, remarkMath];
// throwOnError:false — a partial `$…$` mid-stream must not throw and blank the bubble.
const REHYPE_PLUGINS = [[rehypeKatex, { throwOnError: false }]];

function CopyButton({ value }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  const onCopy = () => {
    navigator.clipboard?.writeText(value).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <button type="button" onClick={onCopy}
      className="rounded-md px-2 py-1 text-[11.5px] text-white/60 hover:bg-white/10 hover:text-white cursor-pointer">
      {copied ? t('copied') : t('copy')}
    </button>
  );
}

/** What a Run printed: text, and images (matplotlib) as pictures. */
export function RunOutput({ run }) {
  const t = useT();
  if (!run) return null;
  if (run.status === 'loading' || run.status === 'running') {
    return <div className="rounded-b-xl border-t border-white/10 bg-[#16181d] px-4 py-2.5 font-mono text-[12.5px] text-white/60">{t(`code.${run.status}`)}</div>;
  }
  const parts = [...outputParts(run.stdout), ...outputParts(run.result && run.result !== 'None' ? run.result : '')];
  const error = run.error || run.stderr;
  return (
    <div className="rounded-b-xl border-t border-white/10 bg-[#16181d] px-4 py-3 text-[12.5px]">
      {parts.map((part, i) => (part.kind === 'image'
        ? <img key={i} src={part.src} alt={t('code.figure')} className="my-2 max-h-80 rounded bg-white" />
        : <pre key={i} className="whitespace-pre-wrap font-mono text-white/85">{part.text}</pre>))}
      {error && <pre className="mt-1 whitespace-pre-wrap font-mono text-[#fca5a5]">{error}</pre>}
      {!parts.length && !error && <span className="font-mono text-white/50">{t('code.noOutput')}</span>}
      <div className="mt-2 text-[11px] text-white/40">{t(run.where === 'server' ? 'code.footerServer' : 'code.footer')}</div>
    </div>
  );
}

// Code is dark in both themes, with a bar: the language, Copy, and Run for Python.
const CodeBlock = memo(function CodeBlock({ language, value, streaming, onRun }) {
  const t = useT();
  const [run, setRun] = useState(null);
  const python = /^(python|py|python3)$/i.test(language || '');
  const start = async () => {
    setRun({ status: 'loading' });
    setRun({ status: 'done', ...(await onRun(value, (phase) => setRun({ status: phase }))) });
  };
  return (
    <div className="my-3 overflow-hidden rounded-xl bg-[#0f1115] text-[13px]">
      <div className="flex items-center justify-between px-3 py-1.5">
        <span className="font-mono text-[11.5px] text-white/50">{language || 'code'}</span>
        <div className="flex items-center gap-1">
          {python && onRun && !streaming && (
            <button type="button" onClick={start} disabled={run?.status === 'loading' || run?.status === 'running'}
              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11.5px] text-white/60 hover:bg-white/10 hover:text-white cursor-pointer disabled:opacity-50">
              <svg className="h-3 w-3" viewBox="0 0 24 24" fill="currentColor"><path d="M7 4.5v15l12-7.5z" /></svg>
              {t('code.run')}
            </button>
          )}
          <CopyButton value={value} />
        </div>
      </div>
      {streaming
        // Mid-stream: plain text, so an unclosed fence cannot blow up memory.
        ? <pre className="overflow-x-auto px-4 pb-3.5 font-mono text-[13px] leading-relaxed text-white/85"><code>{value}</code></pre>
        : <SyntaxHighlighter language={language} style={oneDark} PreTag="div"
            codeTagProps={{ style: { background: 'transparent', fontFamily: 'var(--font-mono)', fontSize: '13px' } }}
            customStyle={{ margin: 0, background: 'transparent', padding: '0 1rem 0.9rem' }}>{value}</SyntaxHighlighter>}
      <RunOutput run={run?.status === 'done' ? { ...run, status: 'ok' } : run} />
    </div>
  );
});

function hostOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return url; }
}

// The components map depends on the message, so it is built per message.
function makeComponents({ streaming, onCite, citeInfo, webSources, onRun }) {
  return {
    pre: ({ children }) => <>{children}</>,
    code({ className, children }) {
      const match = /language-([\w-]+)/.exec(className || '');
      const text = String(children).replace(/\n$/, '');
      const isBlock = Boolean(match) || text.includes('\n');
      if (!isBlock) {
        return <code className="rounded-md bg-ink-surface px-1.5 py-0.5 font-mono text-[0.86em] text-cream">{children}</code>;
      }
      const language = match ? match[1] : '';
      if (!streaming && language === 'mermaid') {
        return <Suspense fallback={<div className="my-3 h-24 rounded-xl bg-ink-surface" />}><Diagram source={text} /></Suspense>;
      }
      if (!streaming && language === 'chart') {
        return <Suspense fallback={<div className="my-3 h-24 rounded-xl bg-ink-surface" />}><Chart source={text} /></Suspense>;
      }
      return <CodeBlock language={language || 'text'} value={text} streaming={streaming} onRun={onRun} />;
    },
    a: ({ children, href }) => {
      if (onCite && href?.startsWith('#cite-')) {
        // An [E#] marker (lib/citations.js linkCitations): open the cited passage.
        const ids = href.slice(6);
        const info = citeInfo?.(ids);
        return (
          <button type="button" onClick={() => onCite(ids)}
            title={info ? `${info.filename} · p.${info.page}${info.text ? `\n${info.text}` : ''}` : ids}
            className="mx-0.5 inline-flex -translate-y-px items-center rounded-md bg-ink-surface px-1.5 align-baseline text-[11.5px] leading-5 text-cream-secondary hover:bg-ink-hover hover:text-cream cursor-pointer">
            {children}
          </button>
        );
      }
      if (href?.startsWith('#web-')) {
        const source = webSources?.get(href.slice(5));
        if (!source) return <span>{children}</span>;
        return (
          <a href={source.url} target="_blank" rel="noreferrer noopener" title={`${source.title}\n${source.url}`}
            className="mx-0.5 inline-flex -translate-y-px items-center gap-1 rounded-md bg-ink-surface px-1.5 align-baseline text-[11.5px] leading-5 text-cream-secondary no-underline hover:bg-ink-hover">
            <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10" /><path d="M2 12h20M12 2a15 15 0 0 1 0 20M12 2a15 15 0 0 0 0 20" /></svg>
            {hostOf(source.url)}
          </a>
        );
      }
      return <a href={href} target="_blank" rel="noreferrer noopener" className="underline decoration-cream-faint underline-offset-2 hover:decoration-cream">{children}</a>;
    },
  };
}

function MessageContent({ content, streaming = false, onCite = null, citeInfo = null, webSources = null, onRun = null }) {
  const components = useMemo(() => makeComponents({ streaming, onCite, citeInfo, webSources, onRun }),
    [streaming, onCite, citeInfo, webSources, onRun]);
  return (
    <div className="prose-chat break-words">
      <ReactMarkdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={REHYPE_PLUGINS} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}

export default memo(MessageContent);
