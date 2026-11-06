import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from extra_sources import mindworking_listings, nordkyst_listings
from geo import SCOPE
from morning import morning_ok, within_hours, receipt, CPH
from refresh import build_snapshot


class Morning(unittest.TestCase):
    def test_retry_window_in_summer_and_winter(self):
        for month in (1, 7):
            for hour in (5, 6, 7, 21, 22):
                now = datetime(2026, month, 6, hour, 17, tzinfo=CPH)
                self.assertEqual(within_hours(now), 6 <= hour < 22)

    def test_today_at_six_even_when_clocks_change(self):
        for month, day in [(3, 29), (10, 25)]:
            now = datetime(2026, month, day, 7, 17, tzinfo=CPH)
            data = {"scope": SCOPE, "sources": {"A":{"status":"ok","updatedAt":now.replace(hour=6).isoformat()}}}
            with patch("morning.SOURCES", {"A":None}):
                self.assertTrue(morning_ok(data, now))
                data["sources"]["A"]["updatedAt"] = now.replace(hour=5).isoformat()
                self.assertFalse(morning_ok(data, now))
                data["sources"]["A"]["status"] = "delayed"
                data["sources"]["A"]["updatedAt"] = now.isoformat()
                self.assertFalse(morning_ok(data, now))

    def test_automatic_retry_requires_every_source_fresh_and_same_scope(self):
        now = datetime(2026, 11, 6, 7, 17, tzinfo=CPH)
        fresh = {"status": "ok", "updatedAt": now.replace(hour=6).isoformat()}
        current = {"scope": dict(SCOPE), "sources": {"A": dict(fresh), "B": dict(fresh)}}
        with patch("morning.SOURCES", {"A": None, "B": None}):
            self.assertTrue(morning_ok(current, now))
            for source in (
                {"status": "ok", "updatedAt": now.replace(hour=0).isoformat()},
                {"status": "ok", "updatedAt": (now + timedelta(minutes=1)).isoformat()},
                {"status": "delayed", "updatedAt": fresh["updatedAt"]},
                {"status": "ok", "updatedAt": None},
            ):
                with self.subTest(source=source):
                    changed = {**current, "sources": {"A": dict(fresh), "B": source}}
                    self.assertFalse(morning_ok(changed, now))
            self.assertFalse(morning_ok({**current, "sources": {"A": dict(fresh)}}, now))
            self.assertFalse(morning_ok({**current, "scope": {**SCOPE, "radiusKm": 20}}, now))

    def test_publication_verification_rejects_previous_or_incomplete_edition(self):
        stamp = "2026-11-06T05:17:00Z"
        published = {
            "scope": dict(SCOPE), "updatedAt": stamp,
            "sources": {"A": {"status": "ok", "count": 1, "updatedAt": stamp}},
            "listings": [{"addressKey": "3400|byvej 1|", "url": "https://example.com/1"}],
        }
        expected = receipt(published)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "published.json")
            command = [sys.executable, str(Path(__file__).with_name("morning.py")), "verify", str(path)]
            env = {**os.environ, "RECEIPT": json.dumps(expected)}
            path.write_text(json.dumps(published))
            self.assertEqual(subprocess.run(command, env=env, capture_output=True).returncode, 0)
            for changed in (
                {**published, "updatedAt": "2026-11-05T23:37:00Z"},
                {**published, "listings": []},
                {**published, "sources": {}},
            ):
                path.write_text(json.dumps(changed))
                with self.subTest(changed=changed):
                    result = subprocess.run(command, env=env, capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("offentlige boligliste", result.stderr)

    def test_audit_identifies_automatic_dispatch_without_changing_source_receipt(self):
        stamp = "2026-11-06T05:17:00Z"
        published = {
            "scope": dict(SCOPE), "updatedAt": stamp,
            "sources": {"A": {"status": "ok", "count": 0, "updatedAt": stamp}},
            "listings": [],
        }
        env = {
            **os.environ, "GITHUB_EVENT_NAME": "workflow_dispatch", "AUTOMATIC": "true",
            "FORCE_REFRESH": "false", "GITHUB_ACTOR": "timer-owner",
            "GITHUB_REPOSITORY": "owner/boligblik", "GITHUB_RUN_ID": "12345", "GITHUB_SHA": "abc123",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "published.json")
            path.write_text(json.dumps(published))
            command = [sys.executable, str(Path(__file__).with_name("morning.py")), "audit", str(path)]
            result = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
        audit = json.loads(result.stdout)
        trigger = audit.pop("trigger")
        self.assertEqual(audit, receipt(published))
        self.assertEqual(trigger, {
            "event": "workflow_dispatch", "automatic": True, "forced": False, "actor": "timer-owner",
            "runUrl": "https://github.com/owner/boligblik/actions/runs/12345", "commitSha": "abc123",
        })

    def test_new_broker_failure_and_recovery(self):
        now = datetime(2026,11,5,6,17,tzinfo=CPH)
        def rows(key):
            return [dict(id=key, address="Lillevej "+key, postalCode="3400", url="https://example.com/"+key, price=1000000)]
        def error(): raise ValueError("offline")
        first = build_snapshot(None, now, {"A":lambda:rows("1")})
        second = build_snapshot(first, now+timedelta(days=1), {"A":lambda:rows("1"),"B":lambda:rows("2")})
        self.assertTrue(all(r["isBaseline"] for r in second["listings"]))
        with redirect_stderr(io.StringIO()):
            third = build_snapshot(second, now+timedelta(days=2), {"A":lambda:rows("1"),"B":error})
        self.assertEqual(third["sources"]["B"]["status"],"delayed")
        self.assertEqual(second["sources"]["B"]["updatedAt"],third["sources"]["B"]["updatedAt"])
        self.assertEqual(len(third["listings"]),2)
        fourth = build_snapshot(third, now+timedelta(days=2,hours=1), {"A":lambda:rows("1"),"B":lambda:rows("3")})
        self.assertEqual(fourth["sources"]["B"]["status"], "ok")
        self.assertFalse(next(r for r in fourth["listings"] if r["source"]=="B")["isBaseline"])

    def test_sold_private_and_distant_houses_are_not_listed(self):
        base = dict(propertyType="Villa", zipCode="3400", salesType="sale", externalStatus="ForSale", isPrivate=True,
                    isBusiness=False, isSold=False, isUnderSale=False, isPropertyOffline=False, isDiscreet=False,
                    latitude=55.93,longitude=12.31,caseUrl="/sag/100/hus/", address="Byvej 1",city="Hillerød",
                    priceSale=5000000,areaLiving=160,rooms=3,imageUrl="https://img.example.com/1")
        catalog = {str(i):dict(base,id=str(i),**flags) for i,flags in enumerate([{}, {"isSold":True},
                    {"isPropertyOffline":True},{"isDiscreet":True},{"latitude":56.3},{"salesType":"rent"}])}
        with patch("extra_sources.fetch", return_value=json.dumps(catalog)):
            result = mindworking_listings("Lokal","example.com","img.example.com","/sag/")
        self.assertEqual([r["address"] for r in result],["Byvej 1, 3400 Hillerød"])

    def test_verified_empty_differs_from_truncated_catalogue(self):
        case = dict(id="a",isActive=True,isSold=False,isRental=False,isCommercial=False,isHomeSale=True,estateType="Helårsgrund")
        def html(total):
            frame=json.dumps({"totalCount":total,"cases":[case]},separators=(",",":"))
            return "<script>self.__next_f.push("+json.dumps([1,frame])+")</script>"
        with patch("extra_sources.fetch",return_value=html(1)):
            self.assertEqual(nordkyst_listings(),[])
        with patch("extra_sources.fetch",return_value=html(2)),self.assertRaises(ValueError):
            nordkyst_listings()
