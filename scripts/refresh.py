"""Refresh the current Hillerød search from the participating brokers."""

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree.ElementTree import ParseError
from zoneinfo import ZoneInfo

from brokers import danbolig_listings, home_listings, lokalbolig_listings


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
    thumbnail = row.get("thumbnail_url")
    if not isinstance(thumbnail, str) or not thumbnail.startswith("https://nybolig.mindworking.eu/api/"):
        thumbnail = None
    return {
        "id": row["id"], "source": "Nybolig", "address": row["address"], "postalCode": row["postal_code"],
        "city": row["city"], "price": number(row["price"]), "area": number(row["living_area"]),
        "rooms": number(row["rooms"]), "url": url, "thumbnailUrl": thumbnail,
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


SOURCES = {"Nybolig": current_listings, "home": home_listings,
           "danbolig": danbolig_listings, "LokalBolig": lokalbolig_listings}


def address_key(row):
    # The first segment is street and house number. Brokers vary in how they
    # append the district. Preserve apartment identifiers when present.
    parts = row["address"].casefold().split(",")
    street = re.sub(r"\s+", " ", parts[0]).strip()
    unit = parts[1].strip() if len(parts) > 1 and re.match(r"^\s*(?:st\.|kld\.|\d{1,2}\.)", parts[1]) else ""
    return f"{row['postalCode']}|{street}|{unit}"


def build_snapshot(previous, now, sources=None):
    sources = SOURCES if sources is None else sources
    today = now.astimezone(ZoneInfo("Europe/Copenhagen")).date().isoformat()
    stamp = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    previous = previous or {}
    old_rows = previous.get("listings", [])
    prior_sources = previous.get("sources", {})
    if previous:
        if previous.get("municipality") != AREA or previous.get("schemaVersion") not in (1, 2):
            raise ValueError("Unexpected previous snapshot.")
        if previous["schemaVersion"] == 1:
            if previous["source"] != "Nybolig":
                raise ValueError("Unexpected legacy source.")
            prior_sources = {"Nybolig": {"updatedAt": previous["updatedAt"]}}
        if datetime.fromisoformat(previous["updatedAt"].replace("Z", "+00:00")) > now:
            raise ValueError("Previous snapshot is newer than this run.")
    old_by_id = {row["id"]: row for row in old_rows}
    old_by_address = {address_key(row): row for row in old_rows}
    if len(old_by_id) != len(old_rows):
        raise ValueError("Duplicate IDs in previous snapshot.")
    source_info = {}
    rows = []
    success = 0
    for name, fetch_rows in sources.items():
        prior = prior_sources.get(name, {})
        try:
            fresh = fetch_rows()
            if not fresh or len({r["id"] for r in fresh}) != len(fresh):
                raise ValueError("Empty or duplicated source results.")
            for row in fresh:
                row["source"] = name
                old = old_by_id.get(row["id"])
                known = old or old_by_address.get(address_key(row))
                row["firstSeen"] = known["firstSeen"] if known else today
                row["isBaseline"] = known["isBaseline"] if known else not prior.get("updatedAt")
                change = old["priceChange"] if old else None
                if old and old["price"] != row["price"]:
                    start = change["previous"] if change and change["date"] == today else old["price"]
                    change = ({"date": today, "previous": start}
                              if start is not None and row["price"] is not None and row["price"] != start else None)
                row["priceChange"] = change
            rows.extend(fresh)
            source_info[name] = {"updatedAt": stamp, "status": "ok", "count": len(fresh)}
            success += 1
        except (KeyError, IndexError, TypeError, ValueError, ParseError, subprocess.SubprocessError) as exc:
            saved = [dict(row, source=name) for row in old_rows if row.get("source", "Nybolig") == name]
            rows.extend(saved)
            source_info[name] = {"updatedAt": prior.get("updatedAt"), "status": "delayed", "count": len(saved)}
            print(f"::warning::{name}: {exc}. Retained {len(saved)} previous listings.", file=sys.stderr)
    if not success:
        raise ValueError("All sources failed; retaining the publicly available snapshot.")
    for row in rows:
        row["addressKey"] = address_key(row)
    return {
        "schemaVersion": 2, "municipality": AREA, "sources": source_info, "updatedAt": stamp,
        "baselineDate": previous.get("baselineDate", today),
        "listings": sorted(rows, key=lambda row: (row["address"], row["id"])),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", type=Path, help="Existing snapshot for comparison.")
    parser.add_argument("--output", type=Path, default=Path("public/boliger.json"))
    args = parser.parse_args()
    prior_path = args.previous or (args.output if args.output.is_file() else None)
    previous = json.loads(prior_path.read_text(encoding="utf-8")) if prior_path else None
    snapshot = build_snapshot(previous, datetime.now(timezone.utc))
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
    print(f"Updated {len(snapshot['listings'])} broker listings at {snapshot['updatedAt']}.")


if __name__ == "__main__":
    main()
