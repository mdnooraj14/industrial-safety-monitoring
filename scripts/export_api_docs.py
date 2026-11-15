"""Export the OpenAPI spec and a readable endpoint reference (docs/openapi.json, docs/api.md)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "backend"):
    sys.path.insert(0, str(p))

from app.main import app  # noqa: E402

INTRO = """# REST API

Generated from the FastAPI OpenAPI specification by `scripts/export_api_docs.py` (do not edit by hand).
Interactive documentation: start the server and open **http://localhost:8000/docs** (Swagger UI) or `/redoc`;
the raw spec is `docs/openapi.json` and `/openapi.json`.

Conventions
- Base path `/api/v1` (plus `/health`). JSON in, JSON out, timestamps ISO-8601 UTC.
- Lists are paginated: `?page=1&page_size=50` (max 500) -> `{items, total, page, page_size, pages}`; filters are query parameters.
- Errors use one envelope: `{"error": {"code": "...", "message": "...", "details": [...]}}`.

| Status | Meaning | Example codes |
|---|---|---|
| 200 / 201 | success / created | |
| 404 | record not found | `NOT_FOUND` |
| 405 | method not allowed (history tables have no PUT / PATCH / DELETE) | `HTTP_ERROR` |
| 409 | duplicate or illegal state change | `DUPLICATE`, `ALREADY_ACKNOWLEDGED`, `INCIDENT_EXISTS`, `INVALID_TRANSITION`, `DUPLICATE_ALARM`, `INCIDENT_CLOSED` |
| 422 | validation or business-rule violation | `VALIDATION_ERROR`, `ACK_BEFORE_ALARM`, `NOT_VERIFIED`, `NO_UNSAFE_STATE`, `EPISODE_TOO_SHORT`, `NO_QUALIFYING_ALARM`, `TIMESTAMP_ORDER`, `PLANT_MISMATCH` |
| 500 | unexpected error (no internals leaked) | `INTERNAL_ERROR` |

"""


def main() -> None:
    spec = app.openapi()
    (ROOT / "docs" / "openapi.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    by_tag: dict[str, list] = {}
    for path, ops in sorted(spec["paths"].items()):
        for method, op in ops.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            params = [p["name"] for p in op.get("parameters", []) if p["in"] == "query" and p["name"] not in ("page", "page_size")]
            body = ""
            if "requestBody" in op:
                ref = op["requestBody"]["content"]["application/json"]["schema"].get("$ref", "")
                body = ref.split("/")[-1]
            by_tag.setdefault((op.get("tags") or ["system"])[0], []).append(
                (method.upper(), path, op.get("summary", ""), ", ".join(params), body))
    lines = [INTRO]
    total = 0
    order = [t["name"] for t in spec.get("tags", [])]
    for tag in order + [t for t in by_tag if t not in order]:
        if tag not in by_tag:
            continue
        lines += [f"## {tag}", "", "| Method | Path | Summary | Filters | Request body |", "|---|---|---|---|---|"]
        for m, p, s, q, b in by_tag[tag]:
            lines.append(f"| {m} | `{p}` | {s} | {q} | {b} |")
            total += 1
        lines.append("")
    lines.insert(1, f"**{total} operations across {len(spec['paths'])} paths.**\n")
    (ROOT / "docs" / "api.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"{total} operations, {len(spec['paths'])} paths -> docs/openapi.json, docs/api.md")


if __name__ == "__main__":
    main()
