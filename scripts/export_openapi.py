"""Write openapi.yaml from the running app's schema:  python scripts/export_openapi.py
tests/test_openapi.py fails if the file and the service ever differ."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("STORAGE_BACKEND", "local")

import yaml  # noqa: E402

from app.main import app  # noqa: E402


def render() -> str:
    return yaml.safe_dump(app.openapi(), sort_keys=False, allow_unicode=True, width=120)


if __name__ == "__main__":
    (ROOT / "openapi.yaml").write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {ROOT / 'openapi.yaml'}")
