import contextlib
import io
import math
import unittest
from datetime import datetime, timezone, timedelta

from geo import LAT, LON, RADIUS_KM, geo_fields
from refresh import build_snapshot


class RadiusRefresh(unittest.TestCase):
    def test_house_coordinates_across_radius_boundary(self):
        for km in (19.99, 20.01):
            delta = math.degrees(km / 6371.0088)
            distance = geo_fields(LAT + delta, LON)["distanceKm"]
            self.assertAlmostEqual(distance, km, places=6)
            self.assertEqual(distance <= RADIUS_KM, km < 20)
        with self.assertRaises(ValueError):
            geo_fields(0, 0)

    def test_migration_and_following_day(self):
        now = datetime(2026, 11, 5, 10, tzinfo=timezone.utc)
        def house(key, price=4500000):
            return dict(id=key, source="A", url="https://example.com/" + key, address="Byvej " + key, postalCode="3400",
                        price=price, **geo_fields(LAT, LON))
        original = dict(house("1"), firstSeen="2026-11-01", isBaseline=False, priceChange=None)
        old = dict(schemaVersion=2, municipality="Hillerød", updatedAt="2026-11-04T10:00:00Z",
                   listings=[original], sources={"A":{"updatedAt":"2026-11-04T10:00:00Z"}})
        def offline():
            raise ValueError("offline")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(ValueError):
            build_snapshot(old, now, {"A":lambda:[house("1")], "B":offline})
        migrated = build_snapshot(old, now, {"A":lambda:[house("1"),house("2")]})
        first, expanded = migrated["listings"]
        self.assertEqual(first["firstSeen"], original["firstSeen"])
        self.assertTrue(expanded["isBaseline"])
        following = build_snapshot(migrated, now + timedelta(days=1),
                                   {"A":lambda:[house("1"),house("2",4250000),house("3")]})
        rows = following["listings"]
        self.assertEqual(rows[1]["price"], 4250000)
        self.assertTrue(rows[1]["isBaseline"])
        self.assertFalse(rows[2]["isBaseline"])
        self.assertEqual(rows[2]["firstSeen"], "2026-11-06")


if __name__ == "__main__":
    unittest.main()
