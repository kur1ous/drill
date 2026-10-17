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
drill review notes/
```

The start screen shows the session's due and new card counts; press any key to begin.
For each card: space shows the answer, then 1 again, 2 hard, 3 good, 4 easy.
Review intervals always satisfy again <= hard < good < easy, with at least a day
between hard, good and easy, including near the 36,500-day maximum.
On the next question, press `u` before revealing the answer to undo the last grade
and return to that card. Undo restores its schedule and removes that review.
Cards graded again return once at the end for practice: space reveals the answer,
then space continues. Practice does not change schedules, history or review counts.
`q` stops; grades already given are kept. Up to 10 new cards are introduced per
day (`--new N` to change it). Use `--limit N` with a positive integer to cap the
distinct cards in a session, due cards first; practice repeats are extra.
Omit it to review the full queue.
Set `NO_COLOR=1` to turn colour off.
Each screen adapts to the current terminal width. Narrow windows wrap the
controls and long code lines; below 40 columns the side margins disappear.

Without installing: `PYTHONPATH=src python -m drill review notes/`.

Run `drill check notes/` to report files with no cards, `##` headings with empty
bodies, and exact duplicate questions within or across files. Locations are
`file:line`, relative to the deck folder; duplicates also name the first occurrence.
Questions are compared as parsed, including case, punctuation and emphasis.
Hidden files and folders are skipped, as in review. The check leaves review history
untouched and exits with code 0 for no issues, 1 for findings, or 2 for read errors.

## Where review history lives

In `.drill.db` inside the deck folder (`--db FILE` to put it elsewhere). It is an
ordinary SQLite file: commit it with your notes or add it to `.gitignore`.
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
