"""Export the running application contract without hand-written duplicate routes."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.main import app


def build_contract():
    # All Week 2 job operations are implemented. Export the runtime schema directly.
    return app.openapi()


if __name__ == "__main__":
    output = ROOT / "docs/design/openapi.json"
    content = json.dumps(build_contract(), ensure_ascii=False, indent=2) + "\n"
    if "--check" in sys.argv:
        if output.read_text() != content:
            raise SystemExit(
                "OpenAPI 已过期，请运行 backend/.venv/bin/python scripts/export-openapi.py"
            )
        print("OpenAPI 与当前模型一致。")
    else:
        output.write_text(content)
        print(output)
