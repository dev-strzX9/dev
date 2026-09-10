"""Import browser library exports through the same validated REST API."""
import argparse
import asyncio
import json
from pathlib import Path
import httpx
from app.schemas import SaveRequest


async def import_items(path, api_url, user, force=False):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("format") != "sop-studio-library" or not isinstance(data.get("items"), list):
        raise ValueError("Expected sop-studio-library with an items array")
    # Validate the entire file before issuing the first write.
    items = []
    for item in data["items"]:
        body = SaveRequest(doc=item["doc"], base_version_no=None if force else 0, saved_by=user, change_note="Browser library import")
        if item.get("sop_no") != body.doc["sop"]["id"]: raise ValueError("SOP number mismatch")
        items.append((item["sop_no"], body))
    failed = 0
    async with httpx.AsyncClient(base_url=api_url.rstrip("/"), timeout=60, headers={"X-User":user}) as client:
        for no, body in items:
            try:
                response = await client.put(f"/api/sops/{no}", json=body.model_dump())
                if response.is_error:
                    failed += 1
                    print(f"FAILED {no}: HTTP {response.status_code} {response.text[:500]}")
                else:
                    result = response.json()
                    print(f"OK {no}: v{result['version_no']}")
                    for warning in result.get("warnings", []): print(f"  WARNING {warning}")
            except httpx.HTTPError as exc:
                failed += 1
                print(f"FAILED {no}: {exc}")
    return failed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--user", default="anonymous")
    parser.add_argument("--force", action="store_true", help="Append a version even when the SOP already exists")
    args = parser.parse_args()
    try: failed = asyncio.run(import_items(args.path,args.api_url,args.user,args.force))
    except (ValueError, KeyError, TypeError, OSError) as exc: parser.exit(1, f"Import failed: {exc}\n")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__": main()
