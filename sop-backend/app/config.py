import json
import os
from dataclasses import dataclass, field
from pathlib import Path

MAX_CONTENT_BYTES = 20 * 1024 * 1024
# The transport envelope includes escaped content, usernames and change notes.
MAX_REQUEST_BYTES = MAX_CONTENT_BYTES + 1024 * 1024


def origins_from_env():
    value = os.getenv("CORS_ORIGINS", "")
    return json.loads(value) if value.startswith("[") else [s.strip() for s in value.split(",") if s.strip()]


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "postgresql://sop:sop@localhost:5432/sop"))
    cors_origins: list[str] = field(default_factory=origins_from_env)
    static_dir: Path = field(default_factory=lambda: Path(os.getenv("STATIC_DIR", str(Path(__file__).resolve().parents[1] / "static"))))
