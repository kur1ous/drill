# drill

Spaced repetition for your own notes, in the terminal. Decks are plain markdown
files in a folder, so they live in git and open in any editor. Scheduling uses
FSRS-4.5. No dependencies beyond Python 3.11.

## Deck format

Every `## heading` is a question; everything under it, up to the next `##`, is
the answer. Anything before the first `##` and any `# title` line is ignored.

```markdown
# Cell biology

## What does the mitochondrion do?

Makes most of the cell's ATP. It has its own DNA, inherited from the mother.

## Why is the plasma membrane described as *fluid*?

- phospholipids drift sideways
- proteins float in the layer

## Write the ATP hydrolysis reaction

    ATP + H2O -> ADP + Pi
```

Files can be nested in folders. A heading with no body is treated as a section
title, not a card. `##` inside a fenced code block is left alone.
Deck files must be UTF-8 text.

## Run

```
pip install -e .          # or: pipx install .
drill init notes/
drill review notes/
```

`drill init DIR` creates the folder (including missing parents) and a two-card
`example.md`, then prints three lines explaining the format. Existing files stay
untouched; if `example.md` already exists, init reports an error and exits with code 2.

The start screen shows the session's due and new card counts; press any key to begin.
For each card: space shows the answer, then 1 again, 2 hard, 3 good, 4 easy.
Review intervals always satisfy again <= hard < good < easy, with at least a day
between hard, good and easy, including near the 36,500-day maximum.
Intervals over two days get a small adjustment before grade ordering to spread reviews out.
The variation is seeded by card ID and review count; displayed intervals match
the saved schedule, including after undoing and repeating a grade.
On the next question, press `u` before revealing the answer to undo the last grade
and return to that card. Undo restores its schedule and removes that review.
Cards graded again return once at the end for practice: space reveals the answer,
then space continues. Practice does not change schedules, history or review counts.
Cards with eight or more lapses show a leech note on the answer screen, suggesting
a rewrite. The summary counts leeches reviewed in this session, including cards
that reach eight lapses during it; practice repeats and undone grades do not count.
`q` stops; grades already given are kept. Up to 10 new cards are introduced per
day (`--new N` to change it). Use `--limit N` with a positive integer to cap the
distinct cards in a session; practice repeats are extra.
Omit it to review the full queue.
The default `--order due` shows oldest due cards first, then new cards in file order.
Use `--order random` to shuffle the queue, or `--order deck` for deck-name order
with cards in source-line order within each deck. These orders can mix due and
new cards. The daily allowance selects new cards in file order before ordering;
`--limit` then takes the first N cards from the ordered queue.
Set `NO_COLOR=1` to turn colour off.
Each screen adapts to the current terminal width. Narrow windows wrap the
controls and long code lines; below 40 columns the side margins disappear.

Without installing: `PYTHONPATH=src python -m drill review notes/`.

Review one file with `drill review notes/biology.md`, or select decks with
`drill review notes/ --only 'unit1/*'`. Patterns are case-sensitive and match
paths relative to the deck folder, using `/` and omitting `.md`; `*` also matches
across folders. Quote patterns so the shell leaves them intact. A file uses its
parent as the deck folder and shares that folder's `.drill.db`; `--db` overrides
this. The whole folder is read before filtering so duplicate questions keep
their IDs. For nested files in a larger collection, use that collection's folder
with `--only` to keep the same deck names and history. No matching cards exits
with code 1. Daily allowances include earlier sessions, even with another filter.

Run `drill check notes/` to report files with no cards, `##` headings with empty
bodies, and exact duplicate questions within or across files. Locations are
`file:line`, relative to the deck folder; duplicates also name the first occurrence.
Questions are compared as parsed, including case, punctuation and emphasis.
Hidden files and folders are skipped, as in review. The check leaves review history
untouched and exits with code 0 for no issues, 1 for findings, or 2 for read errors.

## Where review history lives

Put review defaults in `.drill.toml` in the deck folder (the parent when reviewing
one file):

```toml
new_per_day = 10
retention = 0.9
order = "due"
db = "history.sqlite"
```

All settings are optional. `new_per_day` must be a nonnegative integer; zero
disables new cards. `order` accepts `due`, `random`, or `deck`. Relative `db`
paths resolve from the config's folder. `--new`, `--order`, and `--db` override
these settings; a relative `--db` path resolves from the current directory.
Missing settings keep the usual defaults. Invalid or unreadable config files,
including unknown settings, report the file and exit with code 2.

`retention` is the desired probability of recall, strictly between 0 and 1
(default 0.9). Higher values shorten review intervals; lower values lengthen
them. It affects previews and future grades, leaving existing due dates unchanged.

To use custom FSRS-4.5 weights, set a top-level `weights` array before any
`[deck."..."]` tables. For example, the built-in defaults are:

```toml
weights = [
  0.5701, 1.4436, 4.1386, 10.9355, 5.1443, 1.2006, 0.8627, 0.0362,
  1.629, 0.1342, 1.0166, 2.1174, 0.0839, 0.3204, 1.4676, 0.219, 2.8237,
]
```

Exactly 17 finite numbers are required. Drill accepts these inclusive ranges
(indices start at zero):

| Indices | Range |
| --- | --- |
| 0–3 | 0.01–100 |
| 4 | 1–10 |
| 5, 6, 8, 10, 14 | 0–5 |
| 7, 9, 12, 13, 15 | 0–1 |
| 11 | 0.01–5 |
| 16 | 1–5 |

Weights apply to all decks, including interval previews and future grades of
both new and previously reviewed cards. Existing due dates stay unchanged
until grading; omitting `weights` keeps the built-in defaults.

Per-deck tables override `new_per_day` and `retention` using the same case-sensitive
globs as `--only`:

```toml
[deck."unit1/*"]
new_per_day = 5
retention = 0.95
```

Each matching deck gets its own daily new-card allowance. Decks without a
`new_per_day` override share the top-level allowance, excluding introductions
in decks with their own allowance. Zero disables new cards but keeps due reviews.
When patterns overlap, later tables win for each setting they specify; omitted
settings inherit earlier matches or the top-level default. `--new N` overrides
all new-card settings with one shared database-wide allowance. Retention applies
to both interval previews and saved grades.

By default, review history lives in `.drill.db` inside the deck folder. Use `db`
or `--db FILE` to put it elsewhere. It is an ordinary SQLite file: commit it with
your notes or add it to `.gitignore`.
Database operations wait up to five seconds for another session's lock.
Ctrl-C closes the database, keeping committed grades and rolling back any
unfinished write. During review it shows the stopped summary; if interrupted
during startup or summary output, it exits with code 130.
Unreadable decks and database errors (including corruption or locks) report
the affected file on stderr and exit with code 2, without a traceback.

A card is identified by a hash of its question, ignoring case, spacing, markdown
emphasis and a trailing `?`. So you can rewrite an answer, move a card to another
file or tidy the question's wording without losing its history. Changing the
question in a way that matters (`a^2` to `a_2`, a different noun) makes a new
card. If two cards share a question, each still gets its own history.

## Tests

```
pip install -e ".[dev]"
pytest
```
