"""Has every source actually checked in this morning? Build the public operations receipt."""
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from geo import SCOPE
from refresh import SOURCES

CPH = ZoneInfo("Europe/Copenhagen")


def within_hours(now):
    return 6 <= now.astimezone(CPH).hour < 22


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
    if sys.argv[1] == "within-hours":
        sys.exit(0 if within_hours(datetime.now(CPH)) else 1)
    command, path = sys.argv[1:]
    data = json.loads(Path(path).read_text())
    if command == "current":
        sys.exit(0 if morning_ok(data, datetime.now(CPH)) else 1)
    if command == "verify":
        if receipt(data) != json.loads(os.environ["RECEIPT"]):
            sys.exit("Den offentlige boligliste svarer ikke til den udgivne opdatering.")
        sys.exit(0)
    if command == "audit":
        audit = receipt(data)
        audit["trigger"] = {
            "event": os.environ["GITHUB_EVENT_NAME"],
            "automatic": os.environ["AUTOMATIC"] == "true",
            "forced": os.environ.get("FORCE_REFRESH") == "true",
            "actor": os.environ["GITHUB_ACTOR"],
            "runUrl": f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
            "commitSha": os.environ["GITHUB_SHA"],
        }
        print(json.dumps(audit, ensure_ascii=False, indent=2))
        sys.exit(0)
    if command != "receipt":
        raise ValueError("Unexpected command")
    print(json.dumps(receipt(data), ensure_ascii=False, separators=(",", ":")))
