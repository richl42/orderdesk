"""Settings, read from .env in the project folder and from the environment.

Values already set in the environment win over .env, so tests and one-off
commands can override them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a simple .env file: KEY=value lines, # comments, optional quotes."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def _decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    anthropic_model: str
    app_password: str
    secret_key: str
    data_dir: Path
    ai_price_input_per_mtok: Decimal | None
    ai_price_output_per_mtok: Decimal | None
    operator_name: str
    ai_cache: bool = False  # keep AI answers by request hash (the accuracy test always does)
    backups_dir: Path = PROJECT_DIR / "backups"

    @property
    def ai_prices_set(self) -> bool:
        return self.ai_price_input_per_mtok is not None and self.ai_price_output_per_mtok is not None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "orderdesk.db"

    @property
    def clients_dir(self) -> Path:
        return self.data_dir / "clients"

    @property
    def ai_cache_dir(self) -> Path:
        return self.data_dir / "ai_cache"


@lru_cache
def get_settings() -> Settings:
    env_file = Path(os.environ.get("ORDERDESK_ENV_FILE", PROJECT_DIR / ".env"))
    values = read_env_file(env_file)
    values.update({k: v for k, v in os.environ.items() if v != ""})

    data_dir = Path(values.get("DATA_DIR") or "data")
    if not data_dir.is_absolute():
        data_dir = PROJECT_DIR / data_dir
    backups_dir = Path(values.get("BACKUPS_DIR") or "backups")
    if not backups_dir.is_absolute():
        backups_dir = PROJECT_DIR / backups_dir

    return Settings(
        # ORDERDESK_ANTHROPIC_API_KEY is for cloud build sessions, where ANTHROPIC_API_KEY may not get through.
        anthropic_api_key=values.get("ANTHROPIC_API_KEY") or values.get("ORDERDESK_ANTHROPIC_API_KEY", ""),
        anthropic_model=values.get("ANTHROPIC_MODEL") or "claude-sonnet-5-5",
        app_password=values.get("APP_PASSWORD", ""),
        secret_key=values.get("SECRET_KEY", ""),
        data_dir=data_dir,
        ai_price_input_per_mtok=_decimal(values.get("AI_PRICE_INPUT_PER_MTOK")),
        ai_price_output_per_mtok=_decimal(values.get("AI_PRICE_OUTPUT_PER_MTOK")),
        operator_name=values.get("OPERATOR_NAME") or "Rich",
        ai_cache=(values.get("AI_CACHE") or "").lower() in {"1", "on", "true", "yes"},
        backups_dir=backups_dir,
    )
