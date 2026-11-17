import tomllib
from fnmatch import fnmatchcase
from pathlib import Path


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
        unknown = settings.keys() - {"new_per_day", "order", "db", "retention", "deck"}
        if unknown:
            raise ValueError(f"unknown setting: {sorted(unknown)[0]}")
        _validate_schedule(settings)
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
