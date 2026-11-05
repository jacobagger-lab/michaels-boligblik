"""Refresh the current Hillerød search from Nybolig's public MCP service."""

import argparse
import json
import math
import os
import subprocess
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


ENDPOINT = "https://mcp.nybolig.dk/mcp"
AREA = "Hillerød"
PAGE_SIZE = 100


def search_page(offset):
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "search_properties",
            "arguments": {
                "municipality": AREA,
                "property_type": "Villa",
                "offset": offset,
                "limit": PAGE_SIZE,
            },
        },
    }
    response = subprocess.run(
        [
            "curl", "--silent", "--show-error", "--fail", "--max-time", "35",
            "--retry", "2", "--retry-max-time", "90",
            "--header", "Accept: application/json, text/event-stream",
            "--header", "Content-Type: application/json", "--data-binary", "@-", ENDPOINT,
        ],
        input=json.dumps(request), capture_output=True, check=True, text=True,
    ).stdout.strip()
    if response.startswith("{"):
        messages = [json.loads(response)]
    else:
        # Streamable HTTP returns JSON-RPC responses in SSE data events.
        messages = []
        for event in response.replace("\r\n", "\n").split("\n\n"):
            data = [line[5:].lstrip(" ") for line in event.splitlines() if line.startswith("data:")]
            if data:
                messages.append(json.loads("\n".join(data)))
    reply, = [message for message in messages if message.get("id") == 1]
    result = reply.get("result", {})
    if reply.get("error") or result.get("isError"):
        raise ValueError("Nybolig reported a search error.")
    content, = [part["text"] for part in result["content"] if part["type"] == "text"]
    return json.loads(content)


def number(value):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError("Invalid numeric property field.")
    return value


def listing(row):
    if (row["municipality"], row["property_type"], row["status"]) != (AREA, "Villa", "ForSale"):
        raise ValueError("Unexpected location, property type or status.")
    for key in ("id", "address", "city", "postal_code"):
        if not isinstance(row[key], str) or not row[key].strip():
            raise ValueError("Missing property field: " + key)
    url = row["url"]
    if url is not None:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc not in ("nybolig.dk", "www.nybolig.dk") or not parts.path.startswith("/villa/"):
            raise ValueError("Unexpected listing URL.")
    return {
        "id": row["id"], "address": row["address"], "postalCode": row["postal_code"],
        "city": row["city"], "price": number(row["price"]), "area": number(row["living_area"]),
        "rooms": number(row["rooms"]), "url": url,
    }


def current_listings():
    rows = []
    total = None
    while total is None or len(rows) < total:
        page = search_page(len(rows))
        items = page["properties"]
        if type(page["total"]) is not int or page["total"] < 0 or not isinstance(items, list):
            raise ValueError("Invalid search response.")
        if total is not None and total != page["total"]:
            raise ValueError("The number of listings changed during pagination.")
        total = page["total"]
        if page["offset"] != len(rows) or page["returned"] != len(items) or len(rows) + len(items) > total:
            raise ValueError("Incomplete or inconsistent search response.")
        if not items and len(rows) < total:
            raise ValueError("A non-final page was empty.")
        rows.extend(listing(row) for row in items)
    if len({item["id"] for item in rows}) != len(rows):
        raise ValueError("Duplicated listing IDs.")
    return rows


def build_snapshot(rows, previous, now):
    today = now.astimezone(ZoneInfo("Europe/Copenhagen")).date().isoformat()
    old_rows = {}
    baseline = today
    if previous is not None:
        if (previous["schemaVersion"], previous["source"], previous["municipality"]) != (1, "Nybolig", AREA):
            raise ValueError("Previous snapshot belongs to a different data source or schema.")
        old_rows = {row["id"]: row for row in previous["listings"]}
        if len(old_rows) != len(previous["listings"]):
            raise ValueError("Previous snapshot contains duplicate IDs.")
        baseline = date.fromisoformat(previous["baselineDate"]).isoformat()
        last_update = datetime.fromisoformat(previous["updatedAt"].replace("Z", "+00:00"))
        if last_update > now or baseline > today:
            raise ValueError("Previous snapshot is newer than this execution.")
        if old_rows and not rows:
            raise ValueError("Unexpected empty search; retaining the previous successful snapshot.")

    for row in rows:
        old = old_rows.get(row["id"])
        row["firstSeen"] = old["firstSeen"] if old else today
        row["isBaseline"] = old["isBaseline"] if old else previous is None
        change = old["priceChange"] if old else None
        if old and old["price"] != row["price"]:
            start_price = change["previous"] if change and change["date"] == today else old["price"]
            change = None
            if start_price is not None and row["price"] is not None and row["price"] != start_price:
                change = {"date": today, "previous": start_price}
        row["priceChange"] = change

    return {
        "schemaVersion": 1, "source": "Nybolig", "municipality": AREA,
        "updatedAt": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "baselineDate": baseline,
        "listings": sorted(rows, key=lambda row: (row["address"], row["id"])),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", type=Path, help="Existing snapshot for comparison.")
    parser.add_argument("--output", type=Path, default=Path("public/boliger.json"))
    args = parser.parse_args()
    prior_path = args.previous or (args.output if args.output.is_file() else None)
    previous = json.loads(prior_path.read_text(encoding="utf-8")) if prior_path else None
    snapshot = build_snapshot(current_listings(), previous, datetime.now(timezone.utc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=args.output.parent, encoding="utf-8", delete=False) as temp:
            temp_path = Path(temp.name)
            json.dump(snapshot, temp, ensure_ascii=False, indent=2, allow_nan=False)
            temp.write("\n")
        os.replace(temp_path, args.output)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()
    print(f"Updated {len(snapshot['listings'])} listings from Nybolig at {snapshot['updatedAt']}.")


if __name__ == "__main__":
    main()
