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

## Run

```
pip install -e .          # or: pipx install .
drill review notes/
```

For each card: space shows the answer, then 1 again, 2 hard, 3 good, 4 easy.
`q` stops; grades already given are kept. Up to 10 new cards are introduced per
day (`--new N` to change it). Set `NO_COLOR=1` to turn colour off.

Without installing: `PYTHONPATH=src python -m drill review notes/`.

## Where review history lives

In `.drill.db` inside the deck folder (`--db FILE` to put it elsewhere). It is an
ordinary SQLite file: commit it with your notes or add it to `.gitignore`.

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
