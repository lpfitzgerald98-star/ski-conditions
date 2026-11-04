// Draggable bottom sheets for the phone layout. A sheet rests at one of a few
// heights: drag its grip to move it, or tap or press the grip to step to the next
// height. Above the phone breakpoint the sheet does nothing and CSS lays it out.
//
// A sheet changes its real height rather than sliding off-screen, so a list inside
// it can always scroll all the way to its last row.

const PHONE = window.matchMedia("(max-width: 780px)");
const DRAG_THRESHOLD_PX = 6;      // less movement than this is a tap
const FLICK_PROJECTION_MS = 150;  // how far ahead a flick carries before picking a height
const CLOSE_BELOW_SHARE = 0.6;    // drag under this share of the lowest height to close

export function isPhone() { return PHONE.matches; }

// The notch and home-bar insets in px. env() can't be read directly from script, so
// measure it through a throwaway element.
export function safeInsets() {
  const probe = document.createElement("div");
  probe.style.cssText = "position:fixed;visibility:hidden;" +
    "padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)";
  document.body.appendChild(probe);
  const cs = getComputedStyle(probe);
  const insets = { top: parseFloat(cs.paddingTop) || 0, bottom: parseFloat(cs.paddingBottom) || 0 };
  probe.remove();
  return insets;
}

export function makeSheet(el, grips, { snapHeights, onClose = null, onResize = null }) {
  let index = 0;

  function snapTo(i) {
    if (!isPhone()) {
      el.style.height = "";
      el.removeAttribute("data-snap");
      return;
    }
    const heights = snapHeights();
    index = Math.max(0, Math.min(i, heights.length - 1));
    el.style.height = `${heights[index]}px`;
    el.dataset.snap = String(index);
  }

  function nearest(height, heights) {
    let best = 0;
    heights.forEach((h, i) => {
      if (Math.abs(h - height) < Math.abs(heights[best] - height)) best = i;
    });
    return best;
  }

  grips.forEach(grip => {
    let drag = null;
    let skipClick = false;

    grip.addEventListener("pointerdown", e => {
      if (!isPhone() || e.button !== 0) return;
      drag = { y: e.clientY, height: el.getBoundingClientRect().height,
               lastY: e.clientY, lastT: e.timeStamp, velocity: 0, moved: false };
      grip.setPointerCapture(e.pointerId);
    });

    grip.addEventListener("pointermove", e => {
      if (!drag) return;
      const dy = e.clientY - drag.y;
      if (!drag.moved && Math.abs(dy) < DRAG_THRESHOLD_PX) return;
      if (!drag.moved) {
        drag.moved = true;
        el.classList.add("is-dragging");
      }
      const dt = e.timeStamp - drag.lastT;
      if (dt > 0) drag.velocity = (e.clientY - drag.lastY) / dt;
      drag.lastY = e.clientY;
      drag.lastT = e.timeStamp;
      const heights = snapHeights();
      drag.current = Math.min(heights[heights.length - 1], Math.max(0, drag.height - dy));
      el.style.height = `${drag.current}px`;
    });

    const release = e => {
      if (!drag) return;
      const { moved, velocity, current } = drag;
      drag = null;
      if (!moved) return;
      skipClick = true;
      el.classList.remove("is-dragging");
      const heights = snapHeights();
      const projected = current - velocity * FLICK_PROJECTION_MS;
      if (e.type === "pointerup" && onClose && projected < heights[0] * CLOSE_BELOW_SHARE) {
        onClose();
        return;
      }
      snapTo(nearest(projected, heights));
    };
    grip.addEventListener("pointerup", release);
    grip.addEventListener("pointercancel", release);

    // A tap or the keyboard: step to the next height, wrapping back to the lowest.
    grip.addEventListener("click", () => {
      if (skipClick) { skipClick = false; return; }
      if (!isPhone()) return;
      snapTo((index + 1) % snapHeights().length);
    });
  });

  const refresh = () => {
    snapTo(index);
    if (onResize) onResize();
  };
  PHONE.addEventListener("change", refresh);
  window.addEventListener("resize", refresh);
  refresh();

  return { snapTo };
}
