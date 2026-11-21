from datetime import date, timedelta
from pathlib import Path

from drill.deck import load_deck
from drill.store import Store
from drill.term import Terminal, system_terminal


def review_heatmap(counts: dict[date, int], today: date, terminal: Terminal) -> str:
    start = today - timedelta(days=today.weekday() + 11 * 7)
    lines = ["Reviews per day (12 weeks, local dates)", f"{start} to {today}"]
    for weekday, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
        cells = []
        for week in range(12):
            day = start + timedelta(days=7 * week + weekday)
            count = counts.get(day, 0)
            level = sum(count >= threshold for threshold in (1, 3, 6, 10))
            cells.append(terminal.heat(level) if day <= today else " ")
        lines.append(f"{name} " + " ".join(cells).rstrip())
    legend = "  ".join(
        f"{terminal.heat(level)} {label}"
        for level, label in enumerate(("0", "1-2", "3-5", "6-9", "10+"))
    )
    return "\n".join([*lines, legend])


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
        card_ids = {card.id for card in cards}
        remembered, total = store.retention_counts(card_ids)
        start = today - timedelta(days=today.weekday() + 11 * 7)
        reviews = store.review_counts(card_ids, start, today)
    terminal = system_terminal()
    print(f"Cards: {len(cards)}")
    for category, count in counts.items():
        print(f"  {category}: {count}")
    print(f"Due today (including overdue): {due}")
    retention = f"{remembered / total:.1%} ({remembered}/{total})" if total else "n/a (0 reviews)"
    print(f"Retention: {retention}")
    print()
    print(review_heatmap(reviews, today, terminal))
