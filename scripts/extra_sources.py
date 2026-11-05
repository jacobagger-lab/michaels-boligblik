"""First-party, publicly advertised homes. Do not import third-party portal results."""
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from urllib.parse import urlsplit, urljoin

from brokers import Page, amount, fetch, public_photo
from geo import POSTCODES, RADIUS_KM, geo_fields


def photo(url, host):
    parts = urlsplit(url or "")
    return url if parts.scheme == "https" and parts.netloc == host else None


def estate_listings():
    # This is the public search used by estate.dk/til-salg; our records must be Estate's own.
    args = dict(siteName="estate", isSold=False, isRental=False, postalCodes=sorted(POSTCODES),
                propertyCategories=["Villa"], top=80, scrollToken="")
    rows, seen, tokens = [], set(), set()
    total = None
    for _ in range(250):
        raw = subprocess.run(["curl", "-fsS", "--compressed", "--max-time", "30", "--retry", "2",
                              "-H", "Content-Type: application/json", "--data-binary", "@-",
                              "https://www.estate.dk/api/search/cases/find"], input=json.dumps(args),
                             text=True, capture_output=True, check=True)
        data = json.loads(raw.stdout)
        if total is None:
            total = data["total"]
        cases = data["cases"]
        if not isinstance(total, int) or total <= 0 or data["total"] != total or not cases or data["results"] != len(cases):
            raise ValueError("Incomplete Estate pagination")
        for x in cases:
            key = (x["siteName"], x["id"])
            if key in seen:
                raise ValueError("Repeated Estate search result")
            seen.add(key)
            if x["siteName"] != "estate" or not x["url"].startswith("/villa/"):
                continue
            if x["type"] != "Villa" or x["hasBeenSold"] or x["isRental"] or x["isSaleInProgress"]:
                continue
            match = re.fullmatch(r"/villa/(\d{4})/[^/]+/[\w-]+/[\w-]+/?", x["url"])
            if not match or match[1] not in POSTCODES:
                raise ValueError("Unexpected Estate case URL")
            coords = geo_fields(x["addressLatitude"], x["addressLongitude"])
            if coords["distanceKm"] <= RADIUS_KM:
                rows.append(dict(**coords, id="estate:" + x["id"], source="Estate", url="https://www.estate.dk" + x["url"],
                                 address=x["addressDisplayName"], postalCode=match[1], city=x["addressCity"],
                                 price=amount(x["cashPrice"]), area=amount(x["livingSpace"]), rooms=amount(x["totalNumberOfRooms"]),
                                 thumbnailUrl=photo(x["imageUrl"], "estate.mindworking.eu")))
        if len(seen) == total:
            return rows
        token = data["scrollToken"]
        if len(seen) > total or not token or token in tokens:
            raise ValueError("Invalid Estate continuation")
        tokens.add(token)
        args["scrollToken"] = token
    raise ValueError("Estate pagination limit")


def mindworking_listings(name, domain, media, prefix):
    # Public case-list used by the broker's own published search.
    data = json.loads(fetch("https://" + domain + "/request/caselist/"))
    if not isinstance(data, dict) or not data:
        raise ValueError("Missing case catalogue for " + name)
    rows = []
    for key, x in data.items():
        if (x["propertyType"] != "Villa" or str(x["zipCode"]) not in POSTCODES or x["salesType"] != "sale"
                or x["externalStatus"] != "ForSale" or not x["isPrivate"] or x["isBusiness"] or x["isSold"]
                or x["isUnderSale"] or x["isPropertyOffline"] or x["isDiscreet"]):
            continue
        coords = geo_fields(x["latitude"], x["longitude"])
        if coords["distanceKm"] > RADIUS_KM:
            continue
        url = urljoin("https://" + domain, x["caseUrl"])
        if not url.startswith("https://" + domain + prefix) or url.rstrip("/").endswith("/offline"):
            raise ValueError("Unexpected case URL at " + name)
        rows.append(dict(**coords, id=domain + ":" + str(x["id"]), source=name, url=url,
                         address=f"{x['address']}, {x['zipCode']} {x['city']}", postalCode=str(x["zipCode"]),
                         city=x["city"], price=amount(x["priceSale"]), area=amount(x["areaLiving"]),
                         rooms=amount(x["rooms"]), thumbnailUrl=photo(x["imageUrl"], media)))
    return rows


ELTOFT = "https://eltoftnielsen.dk/boliger/boliger-til-salg/?_types=dk-realequity-estate-villa"


def eltoft_page(page_no):
    html = fetch(ELTOFT + (f"&_paged={page_no}" if page_no > 1 else ""))
    settings = json.JSONDecoder().raw_decode(html.split("window.FWP_JSON = ", 1)[1])[0]
    pager = settings["preload_data"]["settings"]["pager"]
    archive = html.split("<!--fwp-loop-->", 1)[1].split("</section>", 1)[0]
    pairs = set(re.findall(r'<a href="(https://eltoftnielsen\.dk/bolig/[^"/]+/)"[^>]*\bdata-sagsnr="([^"]+)"', archive))
    if (pager["page"] != page_no or not 0 < pager["total_pages"] <= 80 or
            len(pairs) != min(pager["per_page"], pager["total_rows"] - (page_no-1)*pager["per_page"])):
        raise ValueError("Incomplete villa catalogue at Ivan Eltoft Nielsen")
    return pager, pairs


def eltoft_case(pair):
    url, case_id = pair
    markup = fetch(url)
    page = Page(markup)
    if page.meta.get("og:url", "").rstrip("/") != url.rstrip("/"):
        raise ValueError("Unexpected Ivan Eltoft Nielsen listing")
    hero = re.search(r'class="hero-content"[^>]*>\s*<h6>([^<]+)</h6>', markup)
    facts = re.search(r'Specifikationer</h4>\s*<h6>([^<]+)</h6>\s*<ul>(.*?)</ul>', markup, re.S)
    if not facts or not hero:
        raise ValueError("Missing listing details: " + url)
    if facts[1].strip() != "Villa":
        return None
    full = unescape(hero[1]).strip()
    location = re.search(r',\s*(\d{4})\s+([^,]+)$', full)
    if not location and "<google-map-embed" not in markup:
        # The broker also publishes manually written cases without a postcode or map.
        print("Omitted listing without a verifiable location: " + url, file=sys.stderr)
        return None
    if not location:
        raise ValueError("Unexpected address format at " + url)
    if location[1] not in POSTCODES:
        return None
    coords_tag = re.search(r'<google-map-embed\b[^>]*\blat="([\d.]+)"\s+lng="([\d.]+)"', markup)
    if not coords_tag:
        if "<google-map-embed" in markup:
            raise ValueError("Unexpected map format at " + url)
        print("Omitted listing without a verifiable location: " + url, file=sys.stderr)
        return None
    coords = geo_fields(*coords_tag.groups())
    if coords["distanceKm"] > RADIUS_KM:
        return None
    pairs = dict(re.findall(r'<span class="left">([^:]+):\s*</span>\s*<span class="right">([\d.,]+)', facts[2]))
    def value(label):
        return amount(pairs[label].replace(".", "").replace(",", ".")) if label in pairs else None
    if not value("Pris"):
        raise ValueError("No active asking price: " + url)
    return dict(**coords, id="eltoft:" + case_id, source="Ivan Eltoft Nielsen", url=url,
                address=full, postalCode=location[1], city=location[2], price=value("Pris"), area=value("Boligareal"),
                rooms=value("Værelser"), thumbnailUrl=public_photo(page, "eltoftnielsen.dk"))


def eltoft_listings():
    pager, links = eltoft_page(1)
    with ThreadPoolExecutor(max_workers=3) as pool:
        for other, pairs in pool.map(eltoft_page, range(2, pager["total_pages"] + 1)):
            if pager["total_rows"] != other["total_rows"] or links & pairs:
                raise ValueError("Changing Ivan Eltoft Nielsen catalogue")
            links |= pairs
        if len(links) != pager["total_rows"]:
            raise ValueError("Incomplete Ivan Eltoft Nielsen catalogue")
        return [row for row in pool.map(eltoft_case, sorted(links)) if row]


def nordkyst_listings():
    # Published stock on /koeb. Check the advertised total before accepting zero villas.
    html = fetch("https://nordkystmaeglerne.dk/koeb")
    stream = "".join(json.loads(chunk)[1] for chunk in
                     re.findall(r'self\.__next_f\.push\((\[1,.*?\])\)</script>', html, re.S))
    totals = list(re.finditer(r'"totalCount":(\d+),"cases":', stream))
    if len(totals) != 1:
        raise ValueError("Could not verify Nordkystmæglerne catalogue")
    total = totals[0]
    cases = json.JSONDecoder().raw_decode(stream[total.end():])[0]
    links = {u.rsplit("____", 1)[-1]:u for u in re.findall(r'href="(/ejendom/[^"]+____[0-9a-f-]+)"', html)}
    if len(cases) != int(total[1]) or len({x["id"] for x in cases}) != len(cases):
        raise ValueError("Incomplete Nordkystmæglerne catalogue")
    rows = []
    for x in cases:
        if (not x["isActive"] or x["isSold"] or x["isRental"] or x["isCommercial"] or
                not x["isHomeSale"] or x["estateType"] != "Villa"):
            continue
        coords = geo_fields(x["latitude"], x["longitude"])
        if coords["distanceKm"] > RADIUS_KM:
            continue
        postcode, city = x["zipAndCity"].split(" ", 1)
        rows.append(dict(**coords, id="nordkyst:" + x["id"], source="Nordkystmæglerne",
                         url="https://nordkystmaeglerne.dk" + links[x["id"]], address=x["displayName"],
                         postalCode=postcode, city=city, price=amount(x["price"]), rooms=amount(x["numberOfRooms"]),
                         area=amount(x["housingArea"]), thumbnailUrl=photo(x["imageUrl"], "mediaserver.prod.realequity.dk")))
    return rows


MORE_SOURCES = {
    "Estate": estate_listings,
    "Nordkystmæglerne": nordkyst_listings,
    "Ivan Eltoft Nielsen": eltoft_listings,
    "&LIVING": lambda: mindworking_listings("&LIVING", "andliving.dk", "andliving.mindworking.eu", "/sag/"),
    "LILIENHOFF": lambda: mindworking_listings("LILIENHOFF", "www.lilienhoff.dk", "lilienhoff.mindworking.eu", "/liebhaver/"),
}
