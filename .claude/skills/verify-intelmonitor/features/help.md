# Help

Help is a modal that says what each label on a development means (High, Medium, Low, Adverse event, the tone, New, Updated) and lists the keyboard shortcuts; `?` opens it from anywhere outside a text field, and so does `button "Help"` in the sidebar's foot.
High, Medium and Low read their one-sentence definitions from `GET /materiality-levels`.
`button "Priority definitions"` in the `Labels on a development` heading, beside the popup's priority badge and beside each feed card's badge opens Priority definitions: a card per priority with its definition and `Examples:` (`description` and `examples` from `GET /materiality-levels`, written in `PRIORITY_DEFINITIONS` from priority prompt version 8), then `How a priority is set`. Esc closes it alone, leaving Help or the popup open.
New and Updated mean added or updated in the latest weekly run.
`button "Data sources"` beside it opens Data sources: documents stored per source and collection (`GET /data-sources`).

## Sub-features

- `help-open` opens Help with `?`.
- `prio-definitions`: `dialog "Priority definitions"` with regions `High`, `Medium` and `Low` and heading `How a priority is set`.
- `help-content` shows regions `Labels on a development` (one row per label with its meaning) and `Keyboard shortcuts` (⌘K, ?, Esc).

## How to get to it (user POV)

- Press `?` on any page, with focus outside a text field and no modal open.
- Click `Help` or `Data sources` in the sidebar's foot, above the view switcher.

## Driving it with verify

Preconditions:

- `pnpm -C web verify doctor` is ok.
- `pnpm -C web verify open home`, then drive with `agent-browser --session <session>`.

- **Open Help by key.** Press `?`. Run `agent-browser press "?"`. A modal with `heading "Help"` opens with `button "Close"` and regions `Labels on a development` (seven rows) and `Keyboard shortcuts`. No row says "last visit".
- **Close Help.** Press Esc or click `Close`. Run `agent-browser press Escape`. No `dialog[open]` remains and focus returns to where it was.
- **`?` while typing.** Focus search and type `?`. Run `agent-browser press Meta+k` and `agent-browser keyboard type "?"`. The field holds `?` and Help does not open.

## Gotchas

- `?` does nothing while any `dialog[open]` is showing, such as the development popup.
- `agent-browser eval` shares one global scope across calls, so a second `const t = ...` throws `Identifier 't' has already been declared`. Wrap each expression in `(() => { ... })()`.
- The harness browser sometimes stops answering (`Resource temporarily unavailable (os error 35)`, one renderer at 100% CPU) or reports `relaunched browser` and lands on `about:blank`, most often right after `press Escape` on a modal or popover. Run `verify cleanup` and `verify open` again, then redo the step; it did not reproduce on retry.
