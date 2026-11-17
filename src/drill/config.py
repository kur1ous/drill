import tomllib
from pathlib import Path


class ConfigError(ValueError):
    pass


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
        unknown = settings.keys() - {"new_per_day", "order", "db"}
        if unknown:
            raise ValueError(f"unknown setting: {sorted(unknown)[0]}")
        if "new_per_day" in settings:
            count = settings["new_per_day"]
            if type(count) is not int or count < 0:
                raise ValueError("new_per_day must be a nonnegative integer")
        if "order" in settings and settings["order"] not in ("due", "random", "deck"):
            raise ValueError("order must be due, random, or deck")
        if "db" in settings:
            db = settings["db"]
            if not isinstance(db, str) or not db.strip() or "\0" in db:
                raise ValueError("db must be a nonempty file path")
            settings["db"] = folder / db
    except ValueError as exc:
        raise ConfigError(f"invalid config {path}: {exc}") from exc
    return settings
