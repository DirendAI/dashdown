#!/usr/bin/env python3
"""Enrich the bundled world GeoJSON with ISO 3166-1 numeric codes.

Release-only tooling (like ``build-assets.mjs``): it rewrites
``dashdown/static/vendor/world.json`` in place, adding an ``iso`` property
(zero-padded ISO 3166-1 numeric string, e.g. ``"840"``) to every feature whose
Natural Earth name has one. The result is committed, so ``pip install`` users
never run this.

Why: the bundled geometry is keyed by ``name`` (how the ECharts-based MapChart
matches regions), but the SVG geo components (ChoroplethTime, BubbleMap, …)
key countries by ISO numeric code — the join key analytics datasets actually
carry. Enriching the one bundled file lets both families share one geometry.

Geometry provenance: Natural Earth 110m admin-0 (public domain,
https://www.naturalearthdata.com). The name→ISO mapping below was hand-checked
against ISO 3166-1; two Natural Earth entities (``N. Cyprus``, ``Siachen
Glacier``) have no ISO code and are listed in ``NO_ISO`` so the round-trip
check stays exact.

Run from the repo root:  ``python tooling/enrich-world-iso.py``
``tests/test_geo_maps.py`` verifies the committed file matches this mapping.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

WORLD_JSON = (
    Path(__file__).resolve().parent.parent
    / "dashdown" / "static" / "vendor" / "world.json"
)

#: Natural Earth feature name → ISO 3166-1 numeric (zero-padded string).
NAME_TO_ISO: dict[str, str] = {
    "Afghanistan": "004",
    "Aland": "248",
    "Albania": "008",
    "Algeria": "012",
    "American Samoa": "016",
    "Andorra": "020",
    "Angola": "024",
    "Antigua and Barb.": "028",
    "Argentina": "032",
    "Armenia": "051",
    "Australia": "036",
    "Austria": "040",
    "Azerbaijan": "031",
    "Bahamas": "044",
    "Bahrain": "048",
    "Bangladesh": "050",
    "Barbados": "052",
    "Belarus": "112",
    "Belgium": "056",
    "Belize": "084",
    "Benin": "204",
    "Bermuda": "060",
    "Bhutan": "064",
    "Bolivia": "068",
    "Bosnia and Herz.": "070",
    "Botswana": "072",
    "Br. Indian Ocean Ter.": "086",
    "Brazil": "076",
    "Brunei": "096",
    "Bulgaria": "100",
    "Burkina Faso": "854",
    "Burundi": "108",
    "Cambodia": "116",
    "Cameroon": "120",
    "Canada": "124",
    "Cape Verde": "132",
    "Cayman Is.": "136",
    "Central African Rep.": "140",
    "Chad": "148",
    "Chile": "152",
    "China": "156",
    "Colombia": "170",
    "Comoros": "174",
    "Congo": "178",
    "Costa Rica": "188",
    "Croatia": "191",
    "Cuba": "192",
    "Curaçao": "531",
    "Cyprus": "196",
    "Czech Rep.": "203",
    "Côte d'Ivoire": "384",
    "Dem. Rep. Congo": "180",
    "Dem. Rep. Korea": "408",
    "Denmark": "208",
    "Djibouti": "262",
    "Dominica": "212",
    "Dominican Rep.": "214",
    "Ecuador": "218",
    "Egypt": "818",
    "El Salvador": "222",
    "Eq. Guinea": "226",
    "Eritrea": "232",
    "Estonia": "233",
    "Ethiopia": "231",
    "Faeroe Is.": "234",
    "Falkland Is.": "238",
    "Fiji": "242",
    "Finland": "246",
    "Fr. Polynesia": "258",
    "Fr. S. Antarctic Lands": "260",
    "France": "250",
    "Gabon": "266",
    "Gambia": "270",
    "Georgia": "268",
    "Germany": "276",
    "Ghana": "288",
    "Greece": "300",
    "Greenland": "304",
    "Grenada": "308",
    "Guam": "316",
    "Guatemala": "320",
    "Guinea": "324",
    "Guinea-Bissau": "624",
    "Guyana": "328",
    "Haiti": "332",
    "Heard I. and McDonald Is.": "334",
    "Honduras": "340",
    "Hungary": "348",
    "Iceland": "352",
    "India": "356",
    "Indonesia": "360",
    "Iran": "364",
    "Iraq": "368",
    "Ireland": "372",
    "Isle of Man": "833",
    "Israel": "376",
    "Italy": "380",
    "Jamaica": "388",
    "Japan": "392",
    "Jersey": "832",
    "Jordan": "400",
    "Kazakhstan": "398",
    "Kenya": "404",
    "Kiribati": "296",
    "Korea": "410",
    "Kuwait": "414",
    "Kyrgyzstan": "417",
    "Lao PDR": "418",
    "Latvia": "428",
    "Lebanon": "422",
    "Lesotho": "426",
    "Liberia": "430",
    "Libya": "434",
    "Liechtenstein": "438",
    "Lithuania": "440",
    "Luxembourg": "442",
    "Macedonia": "807",
    "Madagascar": "450",
    "Malawi": "454",
    "Malaysia": "458",
    "Mali": "466",
    "Malta": "470",
    "Mauritania": "478",
    "Mauritius": "480",
    "Mexico": "484",
    "Micronesia": "583",
    "Moldova": "498",
    "Mongolia": "496",
    "Montenegro": "499",
    "Montserrat": "500",
    "Morocco": "504",
    "Mozambique": "508",
    "Myanmar": "104",
    "N. Mariana Is.": "580",
    "Namibia": "516",
    "Nepal": "524",
    "Netherlands": "528",
    "New Caledonia": "540",
    "New Zealand": "554",
    "Nicaragua": "558",
    "Niger": "562",
    "Nigeria": "566",
    "Niue": "570",
    "Norway": "578",
    "Oman": "512",
    "Pakistan": "586",
    "Palau": "585",
    "Palestine": "275",
    "Panama": "591",
    "Papua New Guinea": "598",
    "Paraguay": "600",
    "Peru": "604",
    "Philippines": "608",
    "Poland": "616",
    "Portugal": "620",
    "Puerto Rico": "630",
    "Qatar": "634",
    "Romania": "642",
    "Russia": "643",
    "Rwanda": "646",
    "S. Geo. and S. Sandw. Is.": "239",
    "S. Sudan": "728",
    "Saint Helena": "654",
    "Saint Lucia": "662",
    "Samoa": "882",
    "Saudi Arabia": "682",
    "Senegal": "686",
    "Serbia": "688",
    "Seychelles": "690",
    "Sierra Leone": "694",
    "Singapore": "702",
    "Slovakia": "703",
    "Slovenia": "705",
    "Solomon Is.": "090",
    "Somalia": "706",
    "South Africa": "710",
    "Spain": "724",
    "Sri Lanka": "144",
    "St. Pierre and Miquelon": "666",
    "St. Vin. and Gren.": "670",
    "Sudan": "729",
    "Suriname": "740",
    "Swaziland": "748",
    "Sweden": "752",
    "Switzerland": "756",
    "Syria": "760",
    "São Tomé and Principe": "678",
    "Tajikistan": "762",
    "Tanzania": "834",
    "Thailand": "764",
    "Timor-Leste": "626",
    "Togo": "768",
    "Tonga": "776",
    "Trinidad and Tobago": "780",
    "Tunisia": "788",
    "Turkey": "792",
    "Turkmenistan": "795",
    "Turks and Caicos Is.": "796",
    "U.S. Virgin Is.": "850",
    "Uganda": "800",
    "Ukraine": "804",
    "United Arab Emirates": "784",
    "United Kingdom": "826",
    "United States": "840",
    "Uruguay": "858",
    "Uzbekistan": "860",
    "Vanuatu": "548",
    "Venezuela": "862",
    "Vietnam": "704",
    "W. Sahara": "732",
    "Yemen": "887",
    "Zambia": "894",
    "Zimbabwe": "716",
}

#: Natural Earth entities with no ISO 3166-1 assignment: disputed territories,
#: plus two unnamed Kashmir polygons the source geometry carries as ``""``.
NO_ISO = {"N. Cyprus", "Siachen Glacier", ""}


def main() -> int:
    data = json.loads(WORLD_JSON.read_text(encoding="utf-8"))
    names = {f["properties"]["name"] for f in data["features"]}

    unmapped = names - set(NAME_TO_ISO) - NO_ISO
    stale = set(NAME_TO_ISO) - names
    if unmapped:
        print(f"unmapped feature names (add to NAME_TO_ISO or NO_ISO): {sorted(unmapped)}")
        return 1
    if stale:
        print(f"mapping entries with no matching feature: {sorted(stale)}")
        return 1
    codes = list(NAME_TO_ISO.values())
    if len(set(codes)) != len(codes):
        dupes = sorted({c for c in codes if codes.count(c) > 1})
        print(f"duplicate ISO codes in mapping: {dupes}")
        return 1

    for feature in data["features"]:
        name = feature["properties"]["name"]
        iso = NAME_TO_ISO.get(name)
        if iso is not None:
            feature["properties"]["iso"] = iso
        else:
            feature["properties"].pop("iso", None)

    WORLD_JSON.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    enriched = len(names) - len(NO_ISO)
    print(f"enriched {enriched}/{len(names)} features with ISO codes -> {WORLD_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
