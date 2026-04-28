from __future__ import annotations

import os
from pathlib import Path


def get_env_or_file(name: str, default: str | None = None) -> str | None:
    direct = os.getenv(name)
    if direct:
        return direct
    file_path = os.getenv(f"{name}_FILE")
    if file_path:
        path = Path(file_path)
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    return default


def require_env_or_file(name: str) -> str:
    value = get_env_or_file(name)
    if not value:
        raise RuntimeError(f"Missing required secret/config: {name} or {name}_FILE")
    return value

