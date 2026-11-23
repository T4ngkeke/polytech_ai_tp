/**
 * Diagram.jsx — a ```mermaid block of an answer, drawn by Mermaid with its
 * strict security level (no HTML labels, no click handlers). The server has
 * checked the diagram type (backend agent/visuals.py). A source Mermaid
 * cannot read is tried once more with its labels quoted (lib/mermaidRepair.js);
 * if that fails too, the source is shown as code. Mermaid never draws its own
 * error picture (it used to land at the bottom of the page).
 */
import { memo, useEffect, useState } from 'react';
import { useTheme } from '../lib/theme';
import { repairMermaid } from '../lib/mermaidRepair';

let counter = 0;
let loading = null;
const loadMermaid = () => {
  loading = loading || import('mermaid').then((m) => m.default);
  return loading;
};

function Diagram({ source }) {
  const theme = useTheme();
  const [svg, setSvg] = useState(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let current = true;
    loadMermaid().then(async (mermaid) => {
      mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: theme === 'dark' ? 'dark' : 'neutral',
        fontFamily: 'Inter, system-ui, sans-serif', suppressErrorRendering: true });
      let text = source;
      if (!(await mermaid.parse(text, { suppressErrors: true }))) {
        text = repairMermaid(source);
        if (text === source || !(await mermaid.parse(text, { suppressErrors: true }))) throw new Error('unreadable');
      }
      const id = `edullm-diagram-${++counter}`;
      try {
        const { svg: drawn } = await mermaid.render(id, text);
        if (current) { setSvg(drawn); setFailed(false); }
      } finally {
        // render() leaves its scratch element in <body> when it fails.
        document.getElementById(`d${id}`)?.remove();
      }
    }).catch(() => { if (current) setFailed(true); });
    return () => { current = false; };
  }, [source, theme]);
  if (failed) {
    return <pre className="my-3 overflow-x-auto rounded-lg bg-ink-surface p-3 font-mono text-[13px] text-cream-secondary"><code>{source}</code></pre>;
  }
  return (
    <div className="my-3 overflow-x-auto rounded-xl border border-border-subtle bg-ink-raised p-3">
      {svg
        // Mermaid's own SVG, drawn with securityLevel 'strict'.
        ? <div className="flex justify-center [&_svg]:max-w-full [&_svg]:h-auto" dangerouslySetInnerHTML={{ __html: svg }} />
        : <div className="h-24 animate-pulse rounded-lg bg-ink-surface" />}
    </div>
  );
}

export default memo(Diagram);
