/**
 * Scrolling of a chat while an answer is written.
 *
 *  - pin:  after the student sends a question, follow the new answer until its
 *          first line reaches the top of the view, then stay: the student reads
 *          it from the top while the rest is written below;
 *  - tail: the student scrolled to the bottom: follow the newest text;
 *  - off:  the student scrolled elsewhere: do not move.
 */
import { useCallback, useRef } from 'react';

const MARGIN = 12;      // space kept above the answer's first line
const NEAR_BOTTOM = 40;

/** Where to scroll to after the content changed, or null to stay. */
export function scrollTarget(mode, { scrollHeight, clientHeight, latestTop }) {
  const bottom = Math.max(0, scrollHeight - clientHeight);
  if (mode === 'tail') return bottom;
  if (mode === 'pin') return latestTop == null ? bottom : Math.max(0, Math.min(bottom, latestTop - MARGIN));
  return null;
}

/** The mode after the student scrolled by hand. */
export function modeAfterScroll({ scrollTop, scrollHeight, clientHeight }) {
  return scrollHeight - scrollTop - clientHeight < NEAR_BOTTOM ? 'tail' : 'off';
}

/**
 * containerRef: the scrolling message list (position: relative, so offsetTop of
 * a message is measured from it). The latest answer carries data-latest-answer.
 * Returns onScroll for the container, update() after messages change, and
 * follow(mode) when the student sends a question ('pin') or opens a chat ('tail').
 */
export function useChatScroll(containerRef) {
  const mode = useRef('tail');
  const own = useRef(null); // the scrollTop this hook set last

  const onScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    if (own.current !== null && Math.abs(el.scrollTop - own.current) < 2) return;
    own.current = null;
    mode.current = modeAfterScroll(el);
  }, [containerRef]);

  const update = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const latest = el.querySelector('[data-latest-answer]');
    const target = scrollTarget(mode.current, {
      scrollHeight: el.scrollHeight, clientHeight: el.clientHeight,
      latestTop: latest ? latest.offsetTop : null,
    });
    if (target === null || Math.abs(el.scrollTop - target) < 2) return;
    el.scrollTop = target;
    own.current = el.scrollTop;
  }, [containerRef]);

  const follow = useCallback((next) => { mode.current = next; }, []);

  return { onScroll, update, follow };
}
