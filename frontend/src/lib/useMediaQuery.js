import { useSyncExternalStore } from 'react';

/** True while the CSS media query matches, e.g. useMediaQuery('(min-width: 768px)'). */
export default function useMediaQuery(query) {
  return useSyncExternalStore(
    (onChange) => {
      const list = window.matchMedia(query);
      list.addEventListener('change', onChange);
      return () => list.removeEventListener('change', onChange);
    },
    () => window.matchMedia(query).matches,
  );
}
