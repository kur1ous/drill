import tomllib
from fnmatch import fnmatchcase
from pathlib import Path


# Bounds keep stability positive, exponents bounded, and grade modifiers ordered.
_WEIGHT_BOUNDS = (
    *((0.01, 100),) * 4, (1, 10), (0, 5), (0, 5), (0, 1),
    (0, 5), (0, 1), (0, 5), (0.01, 5), (0, 1), (0, 1),
    (0, 5), (0, 1), (1, 5),
)


class ConfigError(ValueError):
    pass


def deck_settings(deck: str, rules: dict) -> dict:
    settings = {}
    for pattern, overrides in rules.items():
        if fnmatchcase(deck, pattern):
            settings.update(overrides)
    return settings


def _validate_schedule(settings: dict) -> None:
    if "new_per_day" in settings:
        count = settings["new_per_day"]
        if type(count) is not int or count < 0:
            raise ValueError("new_per_day must be a nonnegative integer")
    if "retention" in settings:
        retention = settings["retention"]
        if type(retention) not in (int, float) or not 0 < retention < 1:
            raise ValueError("retention must be a number strictly between 0 and 1")


def load_config(folder: Path) -> dict:
    path = folder / ".drill.toml"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    try:
        settings = tomllib.loads(text)
        unknown = settings.keys() - {"new_per_day", "order", "db", "retention", "deck", "weights"}
        if unknown:
            raise ValueError(f"unknown setting: {sorted(unknown)[0]}")
        _validate_schedule(settings)
        if "weights" in settings:
            weights = settings["weights"]
            if not isinstance(weights, list) or len(weights) != 17:
                raise ValueError("weights must be an array of 17 numbers")
            for i, (value, (low, high)) in enumerate(zip(weights, _WEIGHT_BOUNDS)):
                if type(value) not in (int, float) or not low <= value <= high:
                    raise ValueError(f"weights[{i}] must be a finite number between {low} and {high}")
            settings["weights"] = tuple(weights)
        if "order" in settings and settings["order"] not in ("due", "random", "deck"):
            raise ValueError("order must be due, random, or deck")
        rules = settings.get("deck", {})
        if not isinstance(rules, dict):
            raise ValueError("deck must contain tables keyed by glob patterns")
        for pattern, overrides in rules.items():
            try:
                if not pattern.strip() or not isinstance(overrides, dict):
                    raise ValueError("expected a nonempty glob pattern and a settings table")
                unknown = overrides.keys() - {"new_per_day", "retention"}
                if unknown:
                    raise ValueError(f"unknown setting: {sorted(unknown)[0]}")
                _validate_schedule(overrides)
            except ValueError as exc:
                raise ValueError(f"deck.{pattern!r}: {exc}") from exc
        if "db" in settings:
            db = settings["db"]
            if not isinstance(db, str) or not db.strip() or "\0" in db:
                raise ValueError("db must be a nonempty file path")
            settings["db"] = folder / db
    except ValueError as exc:
        raise ConfigError(f"invalid config {path}: {exc}") from exc
    return settings
