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
//   https://tiles.openfreemap.org/styles/liberty    (fuller color -- current)
//   https://tiles.openfreemap.org/styles/positron   (muted greyscale)
//   https://tiles.openfreemap.org/styles/bright
// Liberty is the pick because its color survives the dark-mode invert filter
// (styles.css --map-filter) into a proper DARK basemap; Positron is too light and
// inverts to near-black. In light theme Liberty shows in full color. Swap this
// one line to change the basemap.
export const MAP_STYLE = "https://tiles.openfreemap.org/styles/liberty";

// Initial camera. Only used until the roster loads -- then the map frames all
// pins (map.fitAll on first load), so this is just the pre-fit fallback.
export const MAP_START = { center: [-100, 40], zoom: 2 };

// Zoom the map won't go below/above -- keeps the world from tiling sideways at
// the bottom and stops runaway zoom-in on a single pin.
export const MAP_MIN_ZOOM = 1.4;
export const MAP_MAX_ZOOM = 12;

// Clustering. Pins within CLUSTER_PX screen pixels merge into one count bubble --
// but ONLY below CLUSTER_MAX_ZOOM. At or past that zoom every pin stands alone
// (even a couple overlapping), so you can always zoom in far enough to read every
// individual rating. Cluster-click zooms to at least CLUSTER_MAX_ZOOM to guarantee
// the bubble comes apart.
export const CLUSTER_PX = 28;
export const CLUSTER_MAX_ZOOM = 5.5;

// Marker pixel size (the SVG badge is drawn in a viewBox of this many units).
export const MARKER_SIZE = 28;
