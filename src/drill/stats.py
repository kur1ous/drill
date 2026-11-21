from datetime import date, timedelta
from pathlib import Path

from drill.deck import load_deck
from drill.store import Store
from drill.term import Terminal, system_terminal


def print_cards(folder: Path, db_path: Path, today: date, *, due_only: bool = False) -> None:
    cards = load_deck(folder)
    with Store(db_path) as store:
        states = store.states()
    rows = []
    for card in cards:
        state = states.get(card.id)
        if due_only and (state is None or state.suspended or state.due > today):
            continue
        rows.append((
            " ".join(card.deck.split()),
            "suspended" if state and state.suspended else state.due.isoformat() if state else "new",
            f"{state.stability:.2f}" if state and state.reps else "-",
            str(state.lapses) if state else "0",
            " ".join(card.question.split()),
        ))
    system_terminal()
    if not rows:
        print("No due cards." if due_only else "No cards found.")
        return
    header = ("Deck", "Due", "Stability (days)", "Lapses", "Question")
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(4)]
    for row in [header, *rows]:
        print("  ".join(
            value.rjust(width) if i in (2, 3) else value.ljust(width)
            for i, (value, width) in enumerate(zip(row, widths))
        ) + "  " + row[4])


def forecast_bars(counts: dict[date, int], terminal: Terminal) -> str:
    terminal.refresh_width()
    peak = max(counts.values(), default=0)
    digits = len(str(peak))
    width = max(0, min(40, terminal.width - 14 - digits))
    scale = max(1, peak)
    lines = []
    for day, count in sorted(counts.items()):
        length = max(1, round(count * min(width, peak) / scale)) if count and width else 0
        bar = terminal.dim("█" * length) if length else ""
        lines.append(f"{day}  {count:>{digits}}  {bar}".rstrip())
    return "\n".join(lines)


def print_forecast(folder: Path, db_path: Path, today: date) -> None:
    cards = load_deck(folder)
    counts = {today + timedelta(days=offset): 0 for offset in range(14)}
    overdue = 0
    with Store(db_path) as store:
        states = store.states()
        for card in cards:
            state = states.get(card.id)
            if state is None or state.suspended:
                continue
            if state.due < today:
                overdue += 1
            elif state.due in counts:
                counts[state.due] += 1
    terminal = system_terminal()
    print(f"Overdue: {overdue}")
    print("Scheduled cards (14 days)")
    print(f"{today} to {today + timedelta(days=13)}")
    print(forecast_bars(counts, terminal))


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
            if state is not None and state.suspended:
                counts["suspended"] = counts.get("suspended", 0) + 1
                continue
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
