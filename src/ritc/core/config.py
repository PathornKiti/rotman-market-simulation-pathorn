"""
Configuration: connection settings from `.env`, strategy parameters from `config/<case>.toml`.

Two sources, one rule each:

* `.env` holds things that are *yours* (API key, host, dry-run switch). It is
  gitignored, so a key never lands in a commit.
* `config/<case>.toml` holds things that come from the *case brief* (tickers,
  fees, limits, thresholds). Edit these on competition day when the brief is
  released - no code changes needed.

Real environment variables always win over `.env`, so a one-off override is

    RIT_PORT=9998 python -m ritc doctor
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
ENV_PATH = ROOT / ".env"
CONFIG_DIR = ROOT / "config"

_loaded = False


def load_env(path: Path | str | None = None, override: bool = False) -> dict[str, str]:
    """Parse a .env file into os.environ. A missing file is fine."""
    global _loaded
    p = Path(path) if path else ENV_PATH
    found: dict[str, str] = {}
    _loaded = True
    if not p.exists():
        return found
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, sep, value = line.partition("=")
        if not sep or not key.strip():
            continue
        key, value = key.strip(), value.strip()
        if len(value) > 1 and value[0] in "'\"" and value[-1] == value[0]:
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        found[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return found


def env(key: str, default: str = "") -> str:
    if not _loaded:
        load_env()
    return os.environ.get(key, default).strip()


def env_bool(key: str, default: bool = False) -> bool:
    raw = env(key)
    if raw == "":
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def env_float(key: str, default: float) -> float:
    try:
        return float(env(key) or default)
    except ValueError:
        return default


def api_key() -> str:
    return env("RIT_API_KEY")


def base_url() -> str:
    """RIT_URL if set, else http://RIT_HOST:RIT_PORT/v1 (default localhost:9999)."""
    explicit = env("RIT_URL")
    if explicit:
        return explicit.rstrip("/")
    host = env("RIT_HOST", "localhost")
    if "://" in host:
        host = host.partition("://")[2]
    return f"http://{host.strip('/')}:{env('RIT_PORT', '9999')}/v1"


def describe() -> str:
    key = api_key()
    masked = f"{key[:3]}...{key[-2:]}" if len(key) > 6 else ("SET" if key else "MISSING")
    return f"url={base_url()}  api_key={masked}  env_file={'found' if ENV_PATH.exists() else 'missing'}"


# --------------------------------------------------------------------- TOML
def load_case_config(case: str, path: Path | str | None = None) -> dict[str, Any]:
    """Load config/<case>.toml. Raises FileNotFoundError with a helpful message."""
    p = Path(path) if path else CONFIG_DIR / f"{case}.toml"
    if not p.exists():
        raise FileNotFoundError(f"no strategy config at {p} - copy one from config/ and edit it")
    with p.open("rb") as fh:
        return tomllib.load(fh)
