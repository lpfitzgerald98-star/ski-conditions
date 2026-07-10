// Accessibility helpers: the live-region announcer, reduced-motion detection, and
// small focus utilities used across the UI.

const region = () => document.getElementById("live-region");

// Announce a message to screen readers via the polite aria-live region. Clearing
// first guarantees the same text announced twice still fires.
let clearTimer = null;
export function announce(msg) {
  const el = region();
  if (!el) return;
  el.textContent = "";
  clearTimeout(clearTimer);
  // A tick later so assistive tech registers the change as new content.
  clearTimer = setTimeout(() => { el.textContent = msg; }, 50);
}

export function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// Move focus to an element without the page scrolling to it (the map/card manage
// their own scroll position).
export function focusSilently(el) {
  if (!el) return;
  el.focus({ preventScroll: true });
}
