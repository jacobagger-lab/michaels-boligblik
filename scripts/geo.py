"""A single geographic definition shared by all broker sources."""
import math

LAT, LON, RADIUS_KM = 55.9301, 12.3118, 12
SCOPE = {"latitude": LAT, "longitude": LON, "radiusKm": RADIUS_KM}

# Postcodes intersecting a broad 23 km discovery area (DAGI).
# https://kort.vd.dk/server/rest/services/Grunddata/Danmarks_Administrative_Geografiske_Inddelinger/MapServer/38
# The final inclusion check always uses the house coordinates, never a postcode centroid.
POSTCODES = set("""2730 2740 2750 2760 2765 2800 2820 2830 2840 2850 2860 2880
2930 2942 2950 2960 2970 2980 2990 3000 3050 3060 3070 3080 3100 3120 3140
3150 3200 3210 3220 3230 3250 3300 3310 3320 3330 3360 3370 3390 3400 3450
3460 3480 3490 3500 3520 3540 3550 3600 3630 3650 3660 3670 4000 4040 4050""".split())


def geo_fields(latitude, longitude):
    lat, lon = float(latitude), float(longitude)
    if not (54 <= lat <= 58 and 7 <= lon <= 16):
        raise ValueError("Invalid Danish property coordinates")
    rad = math.radians
    h = math.sin(rad(lat - LAT) / 2)**2 + math.cos(rad(LAT)) * math.cos(rad(lat)) * math.sin(rad(lon - LON) / 2)**2
    return {"latitude": lat, "longitude": lon, "distanceKm": 12742.0176 * math.asin(math.sqrt(h))}
