# Showing visuals

The Visualizer pane is where anything visual goes: charts, diagrams, simulations, animations. Each render tool opens the pane itself, so there is no need to `show` it first. Don't write an `.html` file and ask the user to open it.

## Pick the tool

- **`visualizer.render_html`**: charts, plots, diagrams, SVG, tables, dashboards, anything made of DOM, CSS or SVG. Libraries come from a CDN (`<script src="https://cdn.jsdelivr.net/npm/...">`), e.g. D3, Chart.js, Plotly, Mermaid, or Three.js loaded as a module. This is the default choice.
- **`visualizer.render_js`**: a frame-by-frame animation on the pane's own canvas (`canvas`, `three` or `babylon` mode), using the `{ init, tick, cleanup }` shape its description shows. It is good for continuous motion such as orbits, particles or physics.
- **`visualizer.run_pygame`**: only when the user asks for Python or Pygame. It runs on the backend and streams frames.

## Writing an HTML document

It runs in a sandboxed frame (`allow-scripts` only), with an opaque origin. That means:

- **No `localStorage`, `sessionStorage`, cookies or IndexedDB.** They throw, so keep state in variables.
- **No `alert`, `confirm`, `prompt`, form submission or popups.** Show messages in the page.
- **No access to the app.** You can't call `/api/...` or reach the parent page. Put the data in the document as a JS literal.
- **CDN scripts and public CORS-enabled URLs work.** Nothing else on the network is guaranteed.

Make it fill the pane and look deliberate:

- `html, body { margin: 0; height: 100%; }`. Size to `100vw`/`100vh` or the SVG `viewBox`, never to fixed pixels, and redraw on `resize` when using canvas.
- The frame is white unless you set a background. Set `body { background: #14161a; color: #e6e6e6 }` to match the app's dark surface, unless the user wants something else.
- Label axes and units, and give the chart a title. Use real data where you know it, and say when values are approximate.

**Script errors inside the frame are not reported back.** `visualizer.get_state` will say `hasError: false` even if the page threw. For anything non-trivial, add `window.onerror = (m) => document.body.insertAdjacentText('afterbegin', 'Error: ' + m);` so a failure is visible on screen. Also tell the user what you rendered rather than claiming it ran cleanly.

## Changing what is on screen

Call `visualizer.get_state` and take its `code`, then edit that. Change the smallest thing that satisfies the request and resend it with the **same tool** it was rendered with. Don't rewrite from scratch, and don't ask the user for code that is already loaded.

## After rendering

Reply in a sentence about what is on screen, plus any interaction it supports (hover, zoom, sliders). If the user wants to keep or tweak the source, `visualizer.export_to_editor` opens it as an editable buffer that live-updates the pane.
