---
name: verifier-browser
description: Drive the running web app in a real browser (Playwright/chromium) to verify GUI behavior — especially pointer-drag interactions (Gantt reschedule, bar-resize, drag-from-bucket) that jsdom/Vitest cannot exercise. Use when verifying a frontend change whose surface is the rendered page.
---

# Browser verifier

The planning features are pointer-drag gestures. jsdom (the Vitest env) has no
layout engine and no native drag, so unit tests pass while a real drag is dead.
This skill drives the app in headless chromium to observe the real interaction.

## Prerequisites (already installed)

- `playwright` is a devDependency in `frontend/package.json`.
- Chromium is downloaded (`npx playwright install chromium`, in `~/.cache/ms-playwright`).
- Backend on `http://127.0.0.1:8101`, frontend (Vite) on `http://127.0.0.1:5173`.
  Check both are up: `curl -s -m2 http://127.0.0.1:8101/api/projects` and
  `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5173`.

## How to drive

Write a throwaway ESM script in a temp dir and run it with node. Import
Playwright by absolute path from the frontend's node_modules (it's CommonJS —
use the default-import form):

```js
import pkg from '/abs/path/frontend/node_modules/playwright/index.js'
const { chromium } = pkg
const browser = await chromium.launch()           // headless is fine for screenshots
const page = await browser.newPage({ viewport: { width: 1400, height: 800 } })
page.on('dialog', async (d) => { console.log('DIALOG:', d.message()); await d.dismiss() })
await page.goto('http://127.0.0.1:5173/projects/2/timeline', { waitUntil: 'networkidle' })
```

### Pointer drag (move or resize)

Measure geometry first, then move/down/move/up. A day-column width comes from
`.gantt-col-bg`; a bar is `.gantt-bar`; the resize handle is `.gantt-resize-handle`.

```js
const col = await page.locator('.gantt-col-bg').first().boundingBox()
const bar = page.locator('.gantt-bar').first()
// resize: grab the handle, drag right by N columns
const h = await bar.locator('.gantt-resize-handle').boundingBox()
await page.mouse.move(h.x + h.width/2, h.y + h.height/2)
await page.mouse.down()
await page.mouse.move(h.x + col.width*3, h.y + h.height/2, { steps: 6 })
await page.mouse.up()
// move-drag: grab the bar body instead
```

### Evidence

- DB truth via the API: `fetch('http://127.0.0.1:8101/api/tasks/<id>')` before
  and after — assert `estimated_minutes` / `scheduled_start` changed.
- Screenshots: `await page.screenshot({ path: '/tmp/.../after.png' })`, then Read
  the PNG to confirm the bar span, the axis, and the toast.
- **Reset any data you mutate** at the end (PATCH it back) so the dev DB is left
  as found.

## Gotchas

- Anchors (`<Link>`) are natively draggable; that hijacks the pointer stream so
  window pointer listeners never fire. The Gantt bars set `draggable={false}` —
  if a new draggable element is an `<a>`, it needs the same.
- With only one bar the single day-column flexes very wide; the drag math still
  works because the hook measures that same `.gantt-col-bg` width.
