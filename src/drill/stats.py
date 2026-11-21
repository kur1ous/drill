from datetime import date
from pathlib import Path

from drill.deck import load_deck
from drill.store import Store


def print_stats(folder: Path, db_path: Path, today: date) -> None:
    cards = load_deck(folder)
    counts = dict.fromkeys(("new", "learning", "young", "mature"), 0)
    due = 0
    with Store(db_path) as store:
        states = store.states()
        for card in cards:
            state = states.get(card.id)
            if state is None:
                counts["new"] += 1
                continue
            interval = (state.due - state.last_review).days
            category = "learning" if interval <= 1 else "young" if interval < 21 else "mature"
            counts[category] += 1
            due += state.due <= today
        remembered, total = store.retention_counts({card.id for card in cards})
    print(f"Cards: {len(cards)}")
    for category, count in counts.items():
        print(f"  {category}: {count}")
    print(f"Due today (including overdue): {due}")
    retention = f"{remembered / total:.1%} ({remembered}/{total})" if total else "n/a (0 reviews)"
    print(f"Retention: {retention}")
