/**
 * Tour.jsx — a guided tour: one step at a time, the element it talks about
 * lit up, the rest of the page dimmed. Steps come from lib/tours.js.
 *
 * Props: steps, onClose(finished) — called on Done, Skip or Escape;
 * onNav(open) opens or closes the phone drawer for steps marked `nav`.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

import { TOUR_BUTTONS, boxPosition } from '../lib/tours';

const PAD = 6;       // space around the lit element
const BOX_W = 320;

function visibleRect(target, scroll = false) {
  if (!target) return null;
  const el = [...document.querySelectorAll(`[data-tour="${target}"]`)]
    .find((e) => e.getClientRects().length && e.offsetParent !== null);
  if (!el) return null;
  if (scroll) el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  const r = el.getBoundingClientRect();
  if (r.width === 0 || r.height === 0 || r.bottom < 0 || r.top > innerHeight) return null;
  return { top: r.top - PAD, left: r.left - PAD, width: r.width + 2 * PAD, height: r.height + 2 * PAD };
}

export default function Tour({ steps, onClose, onNav }) {
  const [index, setIndex] = useState(0);
  const [rect, setRect] = useState(null);
  const [boxH, setBoxH] = useState(160);
  const boxRef = useRef(null);
  const step = steps[index];
  const last = index === steps.length - 1;

  const measure = useCallback(() => setRect(visibleRect(step.target)), [step]);

  useEffect(() => {
    onNav?.(!!step.nav);
    // The drawer opens with a short animation: measure after it.
    setRect(visibleRect(step.target, true));
    const t = setTimeout(() => setRect(visibleRect(step.target, true)), 250);
    return () => clearTimeout(t);
  }, [step, onNav, measure]);

  useEffect(() => {
    window.addEventListener('resize', measure);
    window.addEventListener('scroll', measure, true);
    return () => {
      window.removeEventListener('resize', measure);
      window.removeEventListener('scroll', measure, true);
    };
  }, [measure]);

  useLayoutEffect(() => {
    if (boxRef.current) setBoxH(boxRef.current.offsetHeight);
  }, [index, rect]);

  const close = useCallback((finished) => { onNav?.(false); onClose(finished); }, [onClose, onNav]);
  useEffect(() => {
    const key = (e) => {
      if (e.key === 'Escape') close(false);
      if (e.key === 'ArrowRight' && !last) setIndex((i) => i + 1);
      if (e.key === 'ArrowLeft' && index > 0) setIndex((i) => i - 1);
    };
    document.addEventListener('keydown', key);
    return () => document.removeEventListener('keydown', key);
  }, [close, last, index]);

  const pos = boxPosition(rect, { width: BOX_W, height: boxH }, { width: innerWidth, height: innerHeight });
  return (
    <div className="fixed inset-0 z-[60]" role="dialog" aria-modal="true" aria-labelledby="tour-title">
      {rect ? (
        // The lit element: a hole in the dimmed page (its shadow covers the rest).
        <div className="pointer-events-none fixed rounded-xl ring-2 ring-cyan transition-all duration-200"
          style={{ ...rect, boxShadow: '0 0 0 9999px rgba(0, 0, 0, 0.55)' }} />
      ) : (
        <div className="fixed inset-0 bg-black/55" />
      )}
      <div ref={boxRef} className="fixed rounded-xl border border-border-default bg-ink-surface p-4 shadow-elevated transition-all duration-200"
        style={pos}>
        <p className="text-[10px] uppercase tracking-widest text-cream-muted">{index + 1} {TOUR_BUTTONS.of} {steps.length}</p>
        <h3 id="tour-title" className="mt-1 text-sm font-semibold text-cream">{step.title}</h3>
        <p className="mt-1.5 text-sm leading-relaxed text-cream-secondary">{step.text}</p>
        <div className="mt-4 flex items-center gap-2">
          <button type="button" onClick={() => close(false)}
            className="mr-auto cursor-pointer text-xs text-cream-muted hover:text-cream">{TOUR_BUTTONS.skip}</button>
          {index > 0 && (
            <button type="button" onClick={() => setIndex(index - 1)}
              className="cursor-pointer rounded-lg px-3 py-1.5 text-xs text-cream-secondary hover:bg-ink-hover hover:text-cream">
              {TOUR_BUTTONS.back}
            </button>
          )}
          <button type="button" autoFocus onClick={() => (last ? close(true) : setIndex(index + 1))}
            className="cursor-pointer rounded-lg gradient-cyan px-3 py-1.5 text-xs font-semibold text-on-accent hover:brightness-110">
            {last ? TOUR_BUTTONS.done : TOUR_BUTTONS.next}
          </button>
        </div>
      </div>
    </div>
  );
}
