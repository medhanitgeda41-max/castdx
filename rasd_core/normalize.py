"""Column mapping: turns the files banks, insurers and companies really send into the names the assessment reads.

The models never see the uploaded column names. They see a few engineered facts (rain, height, coast distance ...)
that the analyzer builds itself from each asset's latitude and longitude. So the only job of this module is to find,
in a messy file, the handful of things the analyzer needs:

    latitude, longitude            where the asset is (also a single "lat, lon" column)
    collateral_value_aed           what the asset is worth (market value, not forced-sale value)
    outstanding_aed                the part of the loan still unpaid (times the share of the loan this asset secures)
    sum_insured_aed, deductible_aed, perils_covered, other_insurers_cover_aed
    asset_type                     the building material, when the file says it
    asset_id, asset_name, flood_zone, elevation_m, distance_to_coast_km, adaptation_status

It runs on the combined rows before anything else (assess.assess calls it first), so every route into the
analyzer gets it. Order of matching, for each field:
    1. a column the user mapped by hand (column_map)
    2. a column already called what the analyzer expects
    3. a known alias (ALIASES below; spaces, case and punctuation are ignored)
    4. a close spelling (difflib), with the words that must NOT appear (for example "forced" for the asset value)
What was matched, and how, is returned as a report so the person can see it and correct it.

Money is converted to AED with the rates in FX_TO_AED (USD, SAR, QAR, BHD and OMR are pegged; the others are
approximate and should be updated). All figures are screening assumptions, not market quotes.
"""
from __future__ import annotations

import difflib
import math
import re
from datetime import date, datetime
from typing import Any

# --------------------------------------------------------------------------- aliases
# canonical name -> alias spellings. Compared after removing everything but letters and digits.
ALIASES: dict[str, tuple[str, ...]] = {
    "latitude": ("latitude", "lat", "latdd", "latdeg", "latitudedeg", "ycoord", "ycoordinate", "y", "gpslat", "gpslatitude",
                 "geolat", "geolatitude", "locationlat", "locationlatitude", "sitelat", "assetlat", "assetlatitude", "northing"),
    "longitude": ("longitude", "lon", "lng", "long", "londd", "londeg", "longitudedeg", "xcoord", "xcoordinate", "x", "gpslon",
                  "gpslong", "gpslongitude", "geolon", "geolongitude", "locationlon", "locationlongitude", "sitelon",
                  "assetlon", "assetlongitude", "easting"),
    "asset_id": ("assetid", "collateralassetid", "collateralid", "collateralref", "collateralreference", "collateralno",
                 "collateralnumber", "securityid", "securityref", "propertyid", "unitid", "facilityid", "siteid",
                 "assetref", "assetreference", "assetno", "assetnumber", "assetcode"),
    "asset_name": ("assetname", "name", "sitename", "propertyname", "facilityname", "assetdescription", "propertydescription",
                   "collateraldescription", "description"),
    "asset_type": ("assettype", "constructiontype", "assetcategory", "material", "buildingmaterial", "structuretype",
                   "buildingtype", "constructionmaterial", "structure", "construction", "propertytype", "collateraltype",
                   "securitytype", "assetclass", "category"),
    "collateral_value_aed": ("collateralvalueaed", "assetvalueaed", "marketvalue", "marketvalueaed", "collateralvalue",
                             "assetvalue", "currentvalue", "currentmarketvalue", "appraisedvalue", "assessedvalue",
                             "valuationamount", "valuationvalue", "valuation", "fairvalue", "propertyvalue", "securityvalue",
                             "openmarketvalue", "mv", "assetvaluation", "assetvaluationaed", "valuationaed", "replacementvalue", "replacementvalueaed", "bookvalue", "netbookvalue", "carryingvalue",
                             "carryingamount"),
    "outstanding_aed": ("outstandingaed", "outstandingamount", "outstandingbalance", "outstandingprincipal", "currentbalance",
                        "principaloutstanding", "unpaidbalance", "unpaidamount", "balanceoutstanding", "loanbalance",
                        "osbalance", "remainingbalance", "remainingprincipal", "exposure", "currentexposure",
                        "outstandingloan", "outstandingloanamount", "outstandingdebt"),
    "loan_amount_aed": ("sanctionedamount", "loanamount", "facilityamount", "approvedamount", "sanctionedlimit",
                        "approvedlimit", "originalloanamount", "originalamount", "disbursedamount", "principalamount"),
    "sum_insured_aed": ("suminsured", "suminsuredaed", "sumassured", "tsi", "totalsuminsured", "totalinsuredvalue",
                        "insuredamount", "insuredvalue", "coverageamount", "coveragelimit", "policylimit", "limitofliability",
                        "insurancecover", "insurancecoverage", "coverlimit", "coveramount", "insurancecoveramount"),
    "deductible_aed": ("deductible", "deductibleaed", "excess", "policyexcess", "retention", "selfretention", "franchise"),
    "perils_covered": ("perilscovered", "perils", "coveredperils", "risks", "riskscovered", "coveredrisks", "coveragetype",
                       "coveragescope", "coverageincludes", "coverageperils"),
    "other_insurers_cover_aed": ("otherinsurerscover", "otherinsurerscoverage", "coinsurance", "coinsuranceamount",
                                 "reinsurancecover", "reinsurance", "otherinsurersaed",
                                 "alreadyinsured", "alreadyinsuredaed", "alreadyinsuredamount", "existinginsurance", "existinginsuranceaed",
                                 "existingcover", "existingcoverage", "existingcoverageaed", "currentcover", "currentcoverage", "currentinsurance",
                                 "currentinsuranceaed", "insuredelsewhere", "existingpolicyamount", "existinginsuredamount"),
    "annual_premium_aed": ("annualpremium", "annualpremiumaed", "yearlypremium", "agreedpremium", "agreedpremiumaed", "premium", "premiumaed",
                           "premiumperyear", "premiumaedperyear", "agreedpremiumaedperyear", "agreedpremiumperyear", "agreedpremiumpayear", "premiumpayear", "annualpremiumaedperyear", "premiumplan", "premiumpayable", "premiumyear", "yearlypremiumaed", "agreedannualpremium"),
    "adaptation_status": ("adaptationstatus", "mitigationstatus", "protectionstatus", "resiliencestatus",
                          "floodprotectionstatus", "adaptationstage", "measurestatus"),
    "flood_zone": ("floodzone", "floodzoneclass", "floodhazardzone", "floodcategory", "floodriskzone", "femazone"),
    "elevation_m": ("elevation", "elevationm", "heightabovesealevel", "altitude", "altitudem", "groundlevel", "heightm"),
    "distance_to_coast_km": ("distancetocoast", "distancetocoastkm", "coastdistance", "coastdistancekm", "distancefromcoast",
                             "distancetosea", "distancetoseakm", "shoredistance"),
    "distance_to_wadi_km": ("distancetowadi", "distancetowadikm", "wadidistance", "wadidistancekm", "distancetowatercourse",
                            "distancetoriverbed", "distancetonearestwadi"),
    "currency": ("currency", "currencycode", "ccy", "curr", "valuationcurrency", "loancurrency"),
    "collateral_allocation_pct": ("collateralallocationpct", "allocationpct", "allocationpercent", "collateralshare",
                                  "collateralallocation", "sharepct", "coveragepct", "securedshare", "percentallocated"),
    "combined_coordinates": ("coordinates", "coordinate", "latlon", "latlong", "latlng", "geolocation", "gps", "gpscoordinates",
                             "location", "geopoint", "position", "latitudelongitude"),
}

# words that make a column the WRONG one for a field, even if the spelling is close
AVOID: dict[str, tuple[str, ...]] = {
    "collateral_value_aed": ("forced", "liquidation", "fire", "distress", "ratio", "pct", "percent", "date", "due", "change",
                             "appreciation", "depreciation", "rate", "method", "valuer", "id", "no", "number", "insurance",
                             "insured", "ltv", "loan", "outstanding", "sanctioned"),
    "outstanding_aed": ("date", "rate", "interest", "pct", "percent", "ratio", "id", "number", "no", "type", "account"),
    "loan_amount_aed": ("date", "rate", "interest", "pct", "percent", "ratio", "id", "number", "no", "type", "account"),
    "sum_insured_aed": ("date", "expiry", "policyno", "policynumber", "id", "number", "pct", "premium", "status"),
    "deductible_aed": ("date", "id", "number", "pct"),
    "annual_premium_aed": ("date", "id", "number", "pct", "rate", "percent", "status", "total", "sum", "unpaid"),
    "asset_id": ("loan", "borrower", "customer", "owner", "branch", "registration", "valuation", "policy", "account"),
    "asset_type": ("status", "date", "id", "number", "no", "loan", "owner", "branch", "inspection", "legal"),
    "asset_name": ("owner", "borrower", "branch", "valuer", "loan", "insurer", "status"),
    "latitude": ("date", "id", "no", "number"),
    "longitude": ("date", "id", "no", "number"),
    "adaptation_status": ("asset", "legal", "inspection"),
}
# a close spelling is only accepted at or above this similarity
FUZZY_CUTOFF = 0.88

# --------------------------------------------------------------------------- currency
FX_TO_AED: dict[str, float] = {
    "AED": 1.0, "USD": 3.6725, "SAR": 0.9793, "QAR": 1.0088, "BHD": 9.7414, "OMR": 9.5393,       # pegged to the dollar
    "EUR": 4.25, "GBP": 4.90, "KWD": 11.95, "INR": 0.0415, "PKR": 0.013, "EGP": 0.075, "JOD": 5.18,
    "CHF": 4.55, "JPY": 0.024, "CNY": 0.51, "CAD": 2.65, "AUD": 2.40, "SGD": 2.85, "TRY": 0.11,
}
SYMBOLS = {"$": "USD", "US$": "USD", "€": "EUR", "£": "GBP", "د.إ": "AED", "﷼": "SAR", "₹": "INR"}

# --------------------------------------------------------------------------- asset kinds
FINANCIAL_WORDS = ("cash", "deposit", "fixed deposit", "time deposit", "securities", "bond", "bonds", "shares", "equity portfolio",
                   "treasury", "sukuk", "guarantee", "lien on account", "receivable", "receivables", "marketable securities",
                   "gold deposit", "mutual fund", "fund units")
MOVABLE_WORDS = ("vehicle", "car", "truck", "van", "bus", "vessel", "ship", "boat", "aircraft", "equipment", "machinery",
                 "plant", "inventory", "stock", "goods")
MATERIALS = (  # (words found in text, category the risk model knows). Longer, more specific words first.
    (("reinforced concrete", "rcc", "r.c.c", "rc frame", "reinforced"), "reinforced concrete"),
    (("prefab", "prefabricated", "modular", "portable cabin", "porta cabin", "container"), "prefab"),
    (("timber", "wood", "wooden"), "timber"),
    (("tent", "marquee", "tensile", "canvas"), "tent"),
    (("steel", "metal shed", "metal frame", "pre-engineered", "pre engineered", "peb"), "steel"),
    (("masonry", "brick", "block", "stone", "mud brick"), "masonry"),
    (("concrete",), "concrete"),
)

UAE_BOX = (22.0, 26.5, 51.0, 56.8)      # south, north, west, east (rough box around the UAE)


def key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).strip().lower())


def words(value: Any) -> set[str]:
    return set(re.findall(r"[a-z]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", str(value)).lower()))


# --------------------------------------------------------------------------- reading numbers
def parse_amount(value: Any) -> tuple[float | None, str | None]:
    """A number from text such as 'AED 1,200,000', '$450k', '(2,500)', '1.2M'; plus a currency code if the text had one."""
    if value is None or value == "":
        return None, None
    if isinstance(value, (int, float)):
        return (float(value), None) if math.isfinite(float(value)) else (None, None)
    text = str(value).strip()
    if not text or text.lower() in ("n/a", "na", "nan", "none", "null", "-", "--", "unknown", "tbd"):
        return None, None
    currency = None
    for symbol, code in SYMBOLS.items():
        if symbol in text:
            currency, text = code, text.replace(symbol, " ")
    match = re.search(r"\b([A-Za-z]{3})\b", text)
    if match and match.group(1).upper() in FX_TO_AED:
        currency, text = match.group(1).upper(), text.replace(match.group(1), " ")
    negative = text.strip().startswith("(") and text.strip().endswith(")") or text.strip().startswith("-")
    multiplier = 1.0
    suffix = re.search(r"([0-9.,]\s*)(k|m|mn|mm|bn|b)\b", text.lower())
    if suffix:
        multiplier = {"k": 1e3, "m": 1e6, "mn": 1e6, "mm": 1e6, "bn": 1e9, "b": 1e9}[suffix.group(2)]
        text = re.sub(r"(k|m|mn|mm|bn|b)\b", "", text, flags=re.I)
    cleaned = re.sub(r"[^0-9.]", "", text.replace(",", ""))
    if cleaned.count(".") > 1 or cleaned in ("", "."):
        return None, currency
    number = float(cleaned) * multiplier
    return (-number if negative else number), currency


def parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:11].strip() if fmt == "%d %b %Y" else text, fmt).date()
        except ValueError:
            continue
    return None


def parse_pair(value: Any) -> tuple[float, float] | None:
    """'25.2048, 55.2708' or '25.2048 N 55.2708 E' or 'POINT(55.27 25.20)' -> (lat, lon)."""
    text = str(value or "")
    point = re.search(r"POINT\s*\(\s*(-?\d+\.?\d*)\s+(-?\d+\.?\d*)\s*\)", text, re.I)
    if point:
        return float(point.group(2)), float(point.group(1))
    numbers = re.findall(r"-?\d+\.\d+|-?\d+", text)
    if len(numbers) != 2:
        return None
    a, b = float(numbers[0]), float(numbers[1])
    if re.search(r"[SW]\b", text.upper()):
        a, b = (-abs(a) if re.search(r"\d\s*S\b", text.upper()) else a), (-abs(b) if re.search(r"\d\s*W\b", text.upper()) else b)
    return a, b


# --------------------------------------------------------------------------- matching columns
def _good_column(canonical: str, column: str) -> bool:
    bad = AVOID.get(canonical, ())
    found = words(column)
    squashed = key(column)
    return not any(w in found or (len(w) > 4 and w in squashed) for w in bad)


def match_columns(columns: list[str], column_map: dict | None = None) -> dict[str, dict]:
    """canonical -> {"column", "how", "confidence"} for every field that could be matched."""
    by_key: dict[str, str] = {}
    for c in columns:
        by_key.setdefault(key(c), c)
    taken: set[str] = set()
    found: dict[str, dict] = {}

    for canonical, column in (column_map or {}).items():             # 1. chosen by the user
        if canonical in ALIASES and column in columns:
            found[canonical] = {"column": column, "how": "chosen by you", "confidence": 1.0}
            taken.add(column)

    for canonical, aliases in ALIASES.items():                        # 2 and 3. same name, then known alias
        if canonical in found:
            continue
        for rank, alias in enumerate(aliases):
            column = by_key.get(alias)
            if column and column not in taken and (rank < 2 or _good_column(canonical, column)):
                found[canonical] = {"column": column, "how": "same name" if key(canonical) == alias else "known alias",
                                    "confidence": 1.0 if key(canonical) == alias else 0.95}
                taken.add(column)
                break

    for canonical, aliases in ALIASES.items():                        # 4. close spelling
        if canonical in found or canonical == "combined_coordinates":
            continue
        best, best_score = None, 0.0
        for column in columns:
            if column in taken or not _good_column(canonical, column):
                continue
            k = key(column)
            score = max(difflib.SequenceMatcher(None, k, alias).ratio() for alias in aliases)
            if score > best_score:
                best, best_score = column, score
        if best and best_score >= FUZZY_CUTOFF:
            found[canonical] = {"column": best, "how": "close spelling", "confidence": round(best_score, 2)}
            taken.add(best)
    return found


def material_from(*texts: Any) -> str | None:
    blob = " ".join(str(t) for t in texts if t not in (None, "")).lower()
    if not blob:
        return None
    for terms, category in MATERIALS:
        for term in terms:
            if re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", blob):
                return category
    return None


def collateral_kind(*texts: Any) -> str:
    """'financial' (cash, securities: no physical hazard), 'movable' (vehicles, stock) or 'property'."""
    blob = " ".join(str(t) for t in texts if t not in (None, "")).lower()
    if any(re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", blob) for w in FINANCIAL_WORDS):
        return "financial"
    if any(re.search(r"(?<![a-z])" + re.escape(w) + r"s?(?![a-z])", blob) for w in MOVABLE_WORDS):
        return "movable"
    return "property"


STATUS_WORDS = (("complete", 1.0), ("operational", 1.0), ("installed", 1.0), ("done", 1.0), ("in progress", 0.5),
                ("ongoing", 0.5), ("partial", 0.5), ("planned", 0.0), ("proposed", 0.0), ("none", 0.0))


# --------------------------------------------------------------------------- main entry
def normalize_rows(rows: list[dict], column_map: dict | None = None, today: date | None = None) -> tuple[list[dict], dict]:
    """Rows with the analyzer's column names added (the original columns are kept), and a report:
    {"mapping": [...], "warnings": [...], "notes": [...], "converted": n, "not_physical": n}."""
    columns: list[str] = []
    for row in rows:
        for c in row:
            if c not in columns:
                columns.append(c)
    match = match_columns(columns, column_map)
    report: dict = {"mapping": [], "warnings": [], "notes": [], "converted": 0, "not_physical": 0, "outside_uae": 0, "swapped": 0}
    col = lambda name: match[name]["column"] if name in match else None
    fx_unknown: set[str] = set()
    default_ccy = None
    ccy_col = col("currency")
    filled: dict[str, int] = {}
    out: list[dict] = []

    for original in rows:
        row = dict(original)
        row_ccy = (str(original.get(ccy_col)).strip().upper() if ccy_col and original.get(ccy_col) not in (None, "") else default_ccy)

        def put(name: str, value: Any) -> None:
            if value in (None, ""):
                return
            existing = row.get(name)
            if existing in (None, ""):
                row[name] = value
                filled[name] = filled.get(name, 0) + 1

        # --- location
        lat = parse_amount(original.get(col("latitude")))[0] if col("latitude") else None
        lon = parse_amount(original.get(col("longitude")))[0] if col("longitude") else None
        if (lat is None or lon is None) and col("combined_coordinates"):
            pair = parse_pair(original.get(col("combined_coordinates")))
            if pair:
                lat, lon = pair
        if lat is not None and lon is not None:
            in_uae = lambda a, b: UAE_BOX[0] <= a <= UAE_BOX[1] and UAE_BOX[2] <= b <= UAE_BOX[3]
            if (not -90 <= lat <= 90 and -90 <= lon <= 90) or (not in_uae(lat, lon) and in_uae(lon, lat)):
                lat, lon = lon, lat                                      # written the wrong way round
                report["swapped"] += 1
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                put("latitude", lat); put("longitude", lon)
                pass

        # --- money
        def money(name: str) -> float | None:
            column = col(name)
            if not column:
                return None
            number, found_ccy = parse_amount(original.get(column))
            if number is None:
                return None
            return number                                               # money stays in the currency of the file (no conversion)

        put("collateral_value_aed", money("collateral_value_aed"))
        unpaid = money("outstanding_aed")
        if unpaid is None and col("loan_amount_aed") and not col("outstanding_aed"):
            pass                                                         # an original amount is not the unpaid amount: left to assess.py
        share = parse_amount(original.get(col("collateral_allocation_pct")))[0] if col("collateral_allocation_pct") else None
        if unpaid is not None:
            if share is not None and 0 < share <= 100:
                unpaid *= share / 100.0
            put("outstanding_aed", unpaid)
        loan = money("loan_amount_aed")
        if loan is not None:
            put("sanctioned_amount_aed", loan * (share / 100.0 if share and 0 < share <= 100 else 1.0))
        put("sum_insured_aed", money("sum_insured_aed"))
        put("deductible_aed", money("deductible_aed"))
        put("annual_premium_aed", money("annual_premium_aed"))
        put("other_insurers_cover_aed", money("other_insurers_cover_aed"))

        # --- identity and text fields
        for name in ("asset_id", "asset_name", "perils_covered", "flood_zone"):
            if col(name) and original.get(col(name)) not in (None, ""):
                put(name, original.get(col(name)))
        for name in ("elevation_m", "distance_to_coast_km", "distance_to_wadi_km"):
            if col(name):
                number, _ = parse_amount(original.get(col(name)))
                put(name, number)

        # --- what the asset is made of, and whether floods, heat and earthquakes can damage it at all
        type_text = original.get(col("asset_type")) if col("asset_type") else None
        desc_text = original.get(col("asset_name")) if col("asset_name") else None
        remarks = original.get(next((c for c in columns if key(c) in ("remarks", "notes", "comments")), "")) if columns else None
        kind = collateral_kind(type_text, desc_text)
        row["collateral_class"] = kind
        material = material_from(type_text if kind == "property" else None, desc_text, remarks) if kind != "financial" else None
        if material:
            put("asset_type", material)
        if kind == "financial":
            row["physical_hazard_applies"] = "no"
            report["not_physical"] += 1

        # --- adaptation measures
        status_col = col("adaptation_status")
        if status_col and original.get(status_col) not in (None, ""):
            text = str(original.get(status_col)).lower()
            put("adaptation_status", next((w for w, _ in STATUS_WORDS if w in text), original.get(status_col)))
        out.append(row)

    # --- the report
    labels = {"latitude": "Latitude", "longitude": "Longitude", "collateral_value_aed": "Asset value", "outstanding_aed": "Unpaid loan",
              "loan_amount_aed": "Original loan amount (not used as exposure)", "sum_insured_aed": "Sum insured",
              "deductible_aed": "Deductible", "annual_premium_aed": "Agreed premium per year", "perils_covered": "Perils covered", "asset_type": "Asset type / material",
              "asset_id": "Asset ID", "asset_name": "Asset name", "flood_zone": "Flood zone", "elevation_m": "Elevation",
              "distance_to_coast_km": "Distance to coast", "currency": "Currency", "adaptation_status": "Adaptation status",
              "collateral_allocation_pct": "Share of loan secured by this asset", "other_insurers_cover_aed": "Cover by other insurers",
              "combined_coordinates": "Latitude and longitude in one column"}
    for canonical, hit in match.items():
        report["mapping"].append({"field": canonical, "label": labels.get(canonical, canonical), "column": hit["column"],
                                  "how": hit["how"], "confidence": hit["confidence"]})
    if fx_unknown:
        report["warnings"].append("No exchange rate for " + ", ".join(sorted(fx_unknown)) + "; those amounts were used as they are, as if in AED.")
    if report["converted"]:
        report["notes"].append(f"{report['converted']} amounts were converted to AED at the rates in normalize.py (USD 3.6725 is the official peg; update the others when you need them exact).")
    if report["swapped"]:
        report["warnings"].append(f"{report['swapped']} rows had latitude and longitude the wrong way round; they were swapped.")
    if report["outside_uae"]:
        report["warnings"].append(f"{report['outside_uae']} rows are outside the UAE. Weather and earthquake data are still fetched for them, "
                                  "but the hazard and risk models were trained on UAE locations, so treat those scores with care.")
    if report["not_physical"]:
        report["warnings"].append(f"{report['not_physical']} assets are cash, deposits or securities. Floods, heat and earthquakes do not "
                                  "damage them, so they are shown as Not assessed.")
    if not col("collateral_value_aed"):
        report["warnings"].append("No asset value column was found (for example market value, collateral value or asset value).")
    if not col("latitude") and not col("combined_coordinates"):
        report["warnings"].append("No latitude and longitude columns were found. Choose them by hand, or add them to the file.")
    forced = next((c for c in columns if "forced" in key(c)), None)
    if forced:
        report["notes"].append(f"“{forced}” is not used: the asset value is the market value, not the value in a forced sale.")
    return out, report
