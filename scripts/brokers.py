"""Read the brokers' public listings for Hillerød Kommune."""

import json
import re
import subprocess
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

HOME_SEARCH = "https://home.dk/til-salg/villa/region-hovedstaden/hilleroed-kommune/"
DANBOLIG_MAP = "https://danbolig.dk/sitemap.properties.xml"


def fetch(url):
    result = subprocess.run(
        ["curl", "--fail", "--silent", "--show-error", "--location", "--compressed",
         "--proto", "=https", "--proto-redir", "=https", "--max-time", "25",
         "--retry", "2", "--retry-max-time", "50", "--user-agent",
         "MichaelsBoligblik/1.0 (+https://jacobagger-lab.github.io/michaels-boligblik/)", url],
        check=True, capture_output=True,
    )
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


def home_listings():
    urls = set()
    page_no = 1
    seen = set()
    while True:
        page_url = HOME_SEARCH if page_no == 1 else HOME_SEARCH + "?page=" + str(page_no)
        table, result = home_data(Page(fetch(page_url)), "case-catalog-search-p-1")
        hits = table[result["results"]]
        for index in hits:
            item = table[index]
            case_id = (table[item["isExternal"]], table[item["id"]])
            if case_id in seen:
                raise ValueError("Repeated home search result")
            seen.add(case_id)
            if table[item["isExternal"]] or table[item["type"]] != "Villa":
                continue
            url = urljoin("https://home.dk/", table[item["url"]])
            if not re.fullmatch(r"https://home\.dk/salg/huse-villaer/[^/]+/sag-[\w-]+/", url):
                raise ValueError("Unexpected home listing URL")
            urls.add(url)
        if len(seen) == table[result["total"]]:
            break
        if not table[result["hasNextPage"]]:
            raise ValueError("Incomplete home search results")
        if not hits or page_no >= 100:
            raise ValueError("Unexpected pagination at home")
        page_no += 1

    rows = []
    for url in sorted(urls):
        page = Page(fetch(url))
        case_id = url.rstrip("/").rsplit("sag-", 1)[1]
        table, case = home_data(page, "case-" + case_id)
        address = table[case["address"]]
        if (not table[case["isForSale"]] or table[case["isSold"]] or table[case["isExternalCase"]]
                or table[case["isRentalCase"]] or table[case["propertyCategory"]] != "Villa"
                or str(table[address["municipalityNumber"]]) != "219"):
            continue
        stats = table[case["stats"]]
        offer = table[case["offer"]]
        cash = table[offer["cashPrice"]]
        rows.append({
            "id": "home:" + case_id, "source": "home", "url": url,
            "address": table[address["full"]], "postalCode": str(table[address["postalCode"]]),
            "city": table[address["city"]], "price": amount(table[cash["amount"]]),
            "area": amount(table[stats["floorArea"]]), "rooms": amount(table[stats["rooms"]]),
            "thumbnailUrl": public_photo(page, "home.mindworking.eu"),
        })
    return rows


def danbolig_listings():
    sitemap = ElementTree.fromstring(fetch(DANBOLIG_MAP))
    if not sitemap.tag.endswith("}urlset"):
        raise ValueError("Unexpected danbolig sitemap")
    rows = []
    for node in sitemap.findall("{*}url/{*}loc"):
        url = node.text or ""
        # danbolig organises its own case URLs by municipality, then postcode and type.
        match = re.fullmatch(r"https://danbolig\.dk/bolig/hilleroed/(\d{4})/villa/([\w-]+)/", url)
        if not match:
            continue
        markup = fetch(url)
        page = Page(markup)
        blocks = {}
        for block in page.blocks:
            types = block.get("@type", [])
            for name in types if isinstance(types, list) else [types]:
                blocks[name.lower()] = block
        product = blocks["product"]
        offer = product["offers"]
        if offer["availability"].rsplit("/", 1)[-1] != "InStock":
            continue
        if offer["priceCurrency"] != "DKK" or product["url"].rstrip("/") != url.rstrip("/"):
            raise ValueError("Unexpected danbolig offer")
        address = blocks["residence"]["address"]
        city, postal = unescape(address["addressLocality"]), str(address["postalCode"])
        facts = dict(re.findall(r"<tr[^>]*>\s*<td[^>]*>([^<]+)</td>\s*<td[^>]*>([^<]+)</td>", markup))
        if unescape(facts["Type"]).strip() != "Villa" or facts["Udbudsform"].strip() != "Salg":
            continue
        area = unescape(facts["Boligareal"]).split()[0].replace(".", "").replace(",", ".")
        rows.append({
            "id": "danbolig:" + match[2], "source": "danbolig", "url": url,
            "address": f"{unescape(address['streetAddress'])}, {postal} {city}",
            "postalCode": postal, "city": city, "price": amount(offer["price"]),
            "area": amount(area), "rooms": amount(facts.get("Rum")),
            "thumbnailUrl": public_photo(page, "danbolig.mindworking.eu"),
        })
    return rows


def lokalbolig_listings():
    # Postcodes near the municipal boundary are shared with neighbouring councils.
    # Use them to limit requests, then check the council code on each actual case.
    local_postcodes = {"3200", "3230", "3310", "3320", "3330", "3400", "3450",
                       "3480", "3540", "3550", "3600"}
    sitemap = ElementTree.fromstring(fetch("https://www.lokalbolig.dk/bolig/sitemap.xml"))
    if not sitemap.tag.endswith("}urlset"):
        raise ValueError("Unexpected LokalBolig sitemap")
    rows = []
    for node in sitemap.findall("{*}url/{*}loc"):
        url = node.text or ""
        match = re.fullmatch(r"https://www\.lokalbolig\.dk/bolig/villa/[^/]+/(\d{4})/[^/]+/([\w-]+)", url)
        if not match or match[1] not in local_postcodes:
            continue
        markup = fetch(url)
        # The public page embeds the case address (including council code) in its React data.
        codes = set(re.findall(r'"municipalityCode"\s*:\s*"?(\d+)', markup.replace(r'\"', '"')))
        if len(codes) != 1:
            raise ValueError("Could not verify municipality for " + url)
        if int(codes.pop()) != 219:
            continue
        page = Page(markup)
        residence, = [item for block in page.blocks for item in block.get("@graph", [])
                      if item.get("@type") == "SingleFamilyResidence"]
        if re.search(r'"caseStatus":\s*\{\s*"state":\s*"(?:SOLD|RENTED)"', markup.replace(r'\"', '"')):
            continue
        offer = residence["offers"]
        if offer["availability"].rsplit("/", 1)[-1] not in ("InStock", "LimitedAvailability"):
            continue
        if offer["priceCurrency"] != "DKK" or offer["url"].rstrip("/") != url.rstrip("/"):
            raise ValueError("Unexpected LokalBolig offer")
        address = residence["address"]
        city, postal = address["addressLocality"], str(address["postalCode"])
        name = address["streetAddress"]
        if not name.endswith(f", {postal} {city}"):
            name += f", {postal} {city}"
        rows.append({
            "id": "lokalbolig:" + match[2], "source": "LokalBolig", "url": url,
            "address": name, "postalCode": postal, "city": city, "price": amount(offer["price"]),
            "area": amount(residence["floorSize"]["value"]), "rooms": amount(residence.get("numberOfRooms")),
            "thumbnailUrl": public_photo(page, "lokalbolig.io"),
        })
    return rows
