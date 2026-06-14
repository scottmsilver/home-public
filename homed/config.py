# homed/config.py
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    backends: dict = field(default_factory=dict)
    web: dict = field(default_factory=dict)
    home_rows: list = field(default_factory=list)


def load_config(path) -> Config:
    path = Path(path)
    with path.open("rb") as f:  # raises FileNotFoundError if absent
        data = tomllib.load(f)
    return Config(
        backends=data.get("backends", {}),
        web=data.get("web", {}),
        home_rows=data.get("home", {}).get("rows", []),
    )
