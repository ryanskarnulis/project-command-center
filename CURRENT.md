# Current focus — UI/UX revamp: from forms and page-hops to in-place work

> Committed 2026-07-01. The app's data layer and workflows are stable, but the
> interaction model is form-first and navigation-heavy: task cards are one big
> link to a detail page whose right column is an 8-field form wall, creation is
> an 8-field modal, inbox candidate edits detour through the task detail page,
> and the dashboard is a launcher rather than a workspace. This epic replaces
> that grammar with in-place editing: a slide-over peek panel, click-to-edit
> metadata chips, a token-parsing quick-add bar, inline inbox triage, and
> finally a merged working landing screen.
>
> Frontend-only epic: no schema changes, no new AI calls — existing endpoints
> throughout. Each slice is its own reviewable chunk with happy-path tests.

**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Slice 1 — Peek panel + editable metadata chips

*The spine. "Act on a task without leaving where you are."*

- [x] Slide-over task detail panel: clicking a task card opens a right-side
      panel over the current list (Tasks list/board, project tasks, Today)
      instead of navigating away. Esc / click-outside closes. URL updates so
      `/tasks/:id` still deep-links (direct hits render list + open panel).
      *(Peek state is `?task=<id>` on the host page; `/tasks/:id` redirects.)*
- [x] Editable metadata chips replace the "Task Fields" form column: the hero
      badges become the editor — priority pill → 4-option menu, due chip →
      mini calendar with Today/Tomorrow/Next week presets, project pill →
      searchable picker, estimate chip → inline text input, status pill →
      cycle/menu. One click to open, one to set; save-on-choose via the
      existing PATCH path (recurrence edit-scope prompt preserved).
      *(All 8 fields chipped — repeat, assignee, parent task included; the
      form column is gone, no second editing grammar survives.)*
- [x] Chips are shared components (reused by slices 2–3 for quick-add preview
      and inbox candidates). *(`features/tasks/chips/` — controlled
      value/onChange, no Task object required.)*
- [x] Naturally absorbs two TODO improvement ideas: skip / mark-done a
      recurrence where the task shows up, and making the save model legible
      (chip edits are explicit, not blur-magic). *(Skip also lives in the
      status chip menu; estimate/assignee/repeat commits are explicit Set.)*

## Slice 2 — Quick-add bar (token parsing, no modal)

- [x] Permanent one-line input atop Tasks list/board and project task pages:
      `Renew TLS cert fri !high #ops ~20m @ryan` → Enter creates. Tokens parse
      deterministically in TS as you type (priority `!`, project `#`, natural
      dates, estimate `~`, assignee `@`) with a chip preview under the input.
      *(`features/tasks/quickadd/` — parseQuickAdd is a pure first-token-wins
      parser; unrecognized/ambiguous tokens stay literal title text. Chip edits
      override tokens until submit. `#project` files anywhere via the unscoped
      POST /api/tasks, which already honored `project_id`.)*
- [x] "More options" escape hatch opens the full editor prefilled — same
      pattern SubtaskComposer already established for subtasks. *(Both drafts
      share the one modal-defaults handoff in TasksPage.)*
- [x] TaskFormModal stops being the default creation path (kept for the
      escape hatch / edit fallback). *(The toolbar "Add task" button is gone;
      `?new=1` still deep-links the modal.)*
- [x] No AI involved — this is the structured tier; `/new` in the command bar
      remains the AI-extraction tier for messy text.

## Slice 3 — Inline inbox triage

- [x] Candidate cards on the note-review screen become editable in place:
      title as input, project/due/priority as the slice-1 chips. No detour
      through the task detail page. *(`CandidateCard` + override-style drafts
      in `candidateDraft.ts`; description textarea and assignee chip included,
      so every `ReviewEdit` field is editable inline.)*
- [x] Approve/dismiss auto-advances to the next candidate (lowest-confidence
      ordering already in place). *(Decided card leaves the list and focus
      moves to the next card's title input.)*
- [x] Correction capture preserved: field edits persist before the decision
      exactly as today, so `ai_training_examples` rows keep recording the
      user's fixes — the whole point of lowering friction here. *(Edits ride
      the existing `edits` payload on the per-candidate decide endpoint —
      diffed against what the backend would apply anyway; "Approve all"
      carries them too. No backend changes.)*
