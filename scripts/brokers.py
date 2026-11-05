"""Read the brokers' public listings within the configured radius of Hillerød."""

import json
import re
import subprocess
import os
import time
from pathlib import Path
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor
from geo import geo_fields, POSTCODES, RADIUS_KM
from xml.etree import ElementTree

HOME_MAP = "https://home.dk/sitemaps/case.xml"
DANBOLIG_MAP = "https://danbolig.dk/sitemap.properties.xml"


def fetch(url):
    command = [
        "curl", "--fail", "--silent", "--show-error", "--location", "--compressed",
         "--proto", "=https", "--proto-redir", "=https", "--max-time", "25",
         "--retry", "2", "--retry-max-time", "50", "--user-agent",
         "MichaelsBoligblik/1.0 (+https://jacobagger-lab.github.io/michaels-boligblik/)", url,
    ]
    for attempt in range(2):
        result = subprocess.run(command, capture_output=True)
        if attempt == 0 and result.returncode in (18, 35, 52, 55, 56, 92):
            time.sleep(1)
            continue
        result.check_returncode()
        return result.stdout.decode("utf-8-sig")


class Page(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.meta = {}
        self.blocks = []
        self.nuxt = None
        self.kind = None
        self.feed(markup)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            self.meta[attrs.get("property") or attrs.get("name")] = attrs.get("content", "")
        if tag == "script":
            self.kind = "nuxt" if attrs.get("id") == "__NUXT_DATA__" else attrs.get("type")

    def handle_endtag(self, tag):
        if tag == "script":
            self.kind = None

    def handle_data(self, value):
        if self.kind == "nuxt":
            self.nuxt = json.loads(value)
        elif self.kind == "application/ld+json":
            self.blocks.append(json.loads(value))


def public_photo(page, host):
    # Link to the broker's chosen sharing image on its own media server.
    url = page.meta.get("og:image", "")
    parts = urlsplit(url)
    return url if parts.scheme == "https" and parts.netloc == host else None


def amount(value):
    if value in (None, ""):
        return None
    number = float(value)
    if number == 0:
        return None  # Some brokers use zero for undisclosed rooms, area or price.
    if not 0 < number < 1_000_000_000:
        raise ValueError("Unexpected property measurement")
    return int(number) if number.is_integer() else number


def home_data(page, key):
    # Nuxt stores references into a value table. Locate the actual query by its key.
    table = page.nuxt
    app = table[table[0][1]]
    data_ref = table[app["data"]]
    queries = table[data_ref[1]]
    return table, table[queries[key]]


OUTSIDE = object()


def catalog(url, pattern, parse):
    root = ElementTree.fromstring(fetch(url))
    if not root.tag.endswith("}urlset"):
        raise ValueError("Unexpected broker sitemap")
    matches = [(m, node.findtext("{*}lastmod")) for node in root.findall("{*}url")
               if (m := re.fullmatch(pattern, node.findtext("{*}loc") or "")) and m[1] in POSTCODES]
    if not matches:
        raise ValueError("No candidates in broker sitemap")

    # Only reuse verified *out-of-radius* locations, never a listed home's price or status.
    # Revisit them after a sitemap change or one week, whichever happens first.
    cache_dir = os.environ.get("BOLIG_GEO_CACHE")
    path = Path(cache_dir, parse.__name__ + ".json") if cache_dir else None
    try:
        prior = json.loads(path.read_text()) if path and path.exists() else {}
    except (OSError, ValueError):
        prior = {}
    cutoff = time.time() - 7 * 86400
    def read(candidate):
        match, modified = candidate
        saved = prior.get(match[0], {})
        if modified and saved.get("modified") == modified and saved.get("checked", 0) >= cutoff:
            return (match[0], saved)
        try:
            row = parse(match, fetch(match[0]))
            if row is OUTSIDE:
                return (match[0], {"checked": time.time(), "modified": modified})
            return row
        except subprocess.CalledProcessError as exc:
            if exc.returncode == 22 and any(code in (exc.stderr or b"") for code in (b" 404", b" 410")):
                return None  # The listing was withdrawn while the sitemap still linked to it.
            raise
        except (KeyError, TypeError, IndexError, ValueError) as exc:
            raise ValueError(f"{match[0]}: {exc}") from exc

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(read, matches))
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(item for item in results if isinstance(item, tuple))))
    return [row for row in results if isinstance(row, dict)]


def home_listings():
    return catalog(HOME_MAP, r"https://home\.dk/salg/huse-villaer/[^/]*?-(\d{4})-[^/]+/sag-([\w-]+)/", home_case)


def home_case(match, markup):
    url, case_id = match[0], match[2]
    page = Page(markup)
    table, case = home_data(page, "case-" + case_id)
    address = table[case["address"]]
    if (not table[case["isForSale"]] or table[case["isSold"]] or table[case["isExternalCase"]]
            or table[case["isRentalCase"]] or table[case["propertyCategory"]] != "Villa"):
        return None
    coords = geo_fields(table[address["latitude"]], table[address["longitude"]])
    if coords["distanceKm"] > RADIUS_KM:
        return OUTSIDE
    stats, offer = table[case["stats"]], table[case["offer"]]
    cash = table[offer["cashPrice"]]
    return {
        **coords, "id": "home:" + case_id, "source": "home", "url": url,
        "address": table[address["full"]], "postalCode": str(table[address["postalCode"]]),
        "city": table[address["city"]], "price": amount(table[cash["amount"]]),
        "area": amount(table[stats["floorArea"]]), "rooms": amount(table[stats["rooms"]]),
        "thumbnailUrl": public_photo(page, "home.mindworking.eu"),
    }


def danbolig_listings():
    return catalog(DANBOLIG_MAP, r"https://danbolig\.dk/bolig/[^/]+/(\d{4})/villa/([\w-]+)/", danbolig_case)


def danbolig_case(match, markup):
    url = match[0]
    page = Page(markup)
    blocks = {}
    for block in page.blocks:
        types = block.get("@type", [])
        for name in types if isinstance(types, list) else [types]:
            blocks[name.lower()] = block
    product = blocks["product"]
    offer = product["offers"]
    if offer["availability"].rsplit("/", 1)[-1] != "InStock":
        return None
    if offer["priceCurrency"] != "DKK" or product["url"].rstrip("/") != url.rstrip("/"):
        raise ValueError("Unexpected danbolig offer")
    coords = geo_fields(**{k: blocks["residence"]["geo"][k] for k in ("latitude", "longitude")})
    if coords["distanceKm"] > RADIUS_KM:
        return OUTSIDE
    address = blocks["residence"]["address"]
    city, postal = unescape(address["addressLocality"]), str(address["postalCode"])
    facts = dict(re.findall(r"<tr[^>]*>\s*<td[^>]*>([^<]+)</td>\s*<td[^>]*>([^<]+)</td>", markup))
    if unescape(facts["Type"]).strip() != "Villa" or facts["Udbudsform"].strip() != "Salg":
        return None
    area = unescape(facts["Boligareal"]).split()[0].replace(".", "").replace(",", ".")
    return {
        **coords,
        "id": "danbolig:" + match[2], "source": "danbolig", "url": url,
        "address": f"{unescape(address['streetAddress'])}, {postal} {city}",
        "postalCode": postal, "city": city, "price": amount(offer["price"]),
        "area": amount(area), "rooms": amount(facts.get("Rum")),
        "thumbnailUrl": public_photo(page, "danbolig.mindworking.eu"),
    }


def lokalbolig_listings():
    return catalog("https://www.lokalbolig.dk/bolig/sitemap.xml",
                   r"https://www\.lokalbolig\.dk/bolig/villa/[^/]+/(\d{4})/[^/]+/([\w-]+)", lokalbolig_case)


def lokalbolig_case(match, markup):
    url = match[0]
    page = Page(markup)
    residence, = [item for block in page.blocks for item in block.get("@graph", [])
                  if item.get("@type") == "SingleFamilyResidence"]
    if re.search(r'"caseStatus":\s*\{\s*"state":\s*"(?:SOLD|RENTED)"', markup.replace(r'\"', '"')):
        return None
    coords = geo_fields(**{k: residence["geo"][k] for k in ("latitude", "longitude")})
    if coords["distanceKm"] > RADIUS_KM:
        return OUTSIDE
    offer = residence["offers"]
    if offer["availability"].rsplit("/", 1)[-1] not in ("InStock", "LimitedAvailability"):
        return None
    if offer["priceCurrency"] != "DKK" or offer["url"].rstrip("/") != url.rstrip("/"):
        raise ValueError("Unexpected LokalBolig offer")
    address = residence["address"]
    city, postal = address["addressLocality"], str(address["postalCode"])
    name = address["streetAddress"]
    if not name.endswith(f", {postal} {city}"):
        name += f", {postal} {city}"
    return {
        **coords,
        "id": "lokalbolig:" + match[2], "source": "LokalBolig", "url": url,
        "address": name, "postalCode": postal, "city": city, "price": amount(offer["price"]),
        "area": amount(residence["floorSize"]["value"]), "rooms": amount(residence.get("numberOfRooms")),
        "thumbnailUrl": public_photo(page, "lokalbolig.io"),
    }
