# Command Center design kit

Generated preview kit for syncing the app's design system to **claude.ai/design**.

## Source of truth

`app.css` is the app's stylesheet **flattened**: the `src/styles/*.css` partials
concatenated in the exact order [`../frontend/src/index.css`](../frontend/src/index.css)
imports them (the barrel is the source of truth for order; the partials for content).
Do not hand-edit `app.css`. To refresh the kit after the app's styles change:

```sh
scripts/refresh_design_kit.sh
```

then re-sync (below).

## Structure

- `app.css` — shared stylesheet every preview links to.
- `components/<name>/index.html` — one standalone preview page per component group.
  Each page's **first line** is a `<!-- @dsCard group="…" -->` marker; the Design System
  pane builds its card index from those markers (no manual asset registration).

Cards are grouped: **Foundations** (colors, typography, elevation), **Components**
(buttons, inputs, pills, cards, lists, progress, states), **Overlays** (modal).

## Preview locally

Open any `components/<name>/index.html` in a browser — they are plain HTML/CSS, no build
step, no JS.

## Re-sync

Synced via the `DesignSync` tool to the **Command Center Design System** project on
claude.ai/design. Ordering: `list/read` → `finalize_plan` (localDir = this directory,
writes = `app.css`, `README.md`, `components/**/*.html`) → `write_files`.
