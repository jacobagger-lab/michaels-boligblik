"""Has every source actually checked in this morning? Build the public operations receipt."""
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from geo import SCOPE
from refresh import SOURCES

CPH = ZoneInfo("Europe/Copenhagen")


def morning_ok(data, now):
    floor = now.astimezone(CPH).replace(hour=6, minute=0, second=0, microsecond=0)
    if data.get("scope") != SCOPE or set(data.get("sources", {})) != set(SOURCES):
        return False
    for source in data["sources"].values():
        if source.get("status") != "ok" or not source.get("updatedAt"):
            return False
        checked = datetime.fromisoformat(source["updatedAt"].replace("Z", "+00:00"))
        if not floor <= checked <= now:
            return False
    return True


def receipt(data):
    return {"checkedAt": data["updatedAt"], "radiusKm": data["scope"]["radiusKm"],
            "uniqueLinkedHomes": len({r["addressKey"] for r in data["listings"] if r["url"]}),
            "sources": {name: {k:s[k] for k in ("status","count","updatedAt")}
                        for name, s in data["sources"].items()}}


if __name__ == "__main__":
    command, path = sys.argv[1:]
    data = json.loads(Path(path).read_text())
    if command == "current":
        sys.exit(0 if morning_ok(data, datetime.now(CPH)) else 1)
    if command != "receipt":
        raise ValueError("Unexpected command")
    print(json.dumps(receipt(data), ensure_ascii=False, separators=(",", ":")))
