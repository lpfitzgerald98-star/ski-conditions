// Runtime configuration. The one file you edit to change hosting mode or map look.

// Data source. Leave API_BASE empty for the STATIC build (flat JSON under data/,
// what GitHub Pages serves). Set it to a running backend's origin
// (e.g. "https://ski.fly.dev") to switch the whole app to the live SSE stream --
// no other change needed; sse.js and api.js both key off this.
export const API_BASE = "";

// Derived: are we live (SSE + per-request scoring) or static (daily snapshot)?
export const LIVE = API_BASE !== "";

// Where the static snapshot lives, relative to the page.
export const DATA_BASE = "data";

// Base map. Any OpenFreeMap style URL works, no API key:
//   https://tiles.openfreemap.org/styles/liberty   (fuller color -- current pick)
//   https://tiles.openfreemap.org/styles/positron  (muted greyscale)
//   https://tiles.openfreemap.org/styles/bright
// Swap this one line to change the basemap.
export const MAP_STYLE = "https://tiles.openfreemap.org/styles/liberty";

// Initial camera.
export const MAP_START = { center: [-100, 46], zoom: 3.2 };

// Marker pixel size (the SVG badge is drawn in a viewBox of this many units).
export const MARKER_SIZE = 30;
