"""Combining the files a person adds, the way the RASD website did it.

The first file (loans, then asset register, then locations, then insurance ... see ORDER) is the base. Every other file is
joined onto it as a left join on the asset ID, or else the site ID (name of the column is recognised from a list of
aliases). A file with several rows for one asset keeps the newest row (by a valuation / as-of date) or else the first.
Columns that both files have are merged: the first non-empty value wins. A file that shares no ID with the others is NOT
combined and is reported. When no file has an ID column, files are joined on the asset name instead (and that is reported).
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

KEY_ALIASES = {"assetid": "asset", "collateralassetid": "asset", "collateralid": "asset", "collateralref": "asset",
               "collateralreference": "asset", "collateralno": "asset", "collateralnumber": "asset", "securityid": "asset",
               "propertyid": "asset", "assetref": "asset", "assetreference": "asset", "assetno": "asset", "assetnumber": "asset",
               "assetcode": "asset", "siteid": "site", "sitecode": "site", "locationid": "site",
               "assetname": "name", "name": "name", "sitename": "name", "propertyname": "name", "facilityname": "name"}
JOIN_PRIORITY = ("asset", "site", "name")
LABELS = {"asset": "asset ID", "site": "site ID", "name": "asset name"}
# the order the website combined the file types in
ORDER = ["loans", "register", "locations", "insurance", "financial", "adaptation", "other"]


def _norm(v) -> str:
    return re.sub(r"[^a-z0-9]", "", str(v if v is not None else "").lower())


def _file_kind(name: str, columns: list[str]) -> str:
    n, cols = _norm(name), " ".join(_norm(c) for c in columns)
    if any(w in n for w in ("loan", "collateral", "credit")) or any(w in cols for w in ("outstanding", "unpaid", "loanamount")):
        return "loans"
    if any(w in n for w in ("insur", "polic", "cover")) or any(w in cols for w in ("suminsured", "deductible", "perils")):
        return "insurance"
    if any(w in n for w in ("register", "assets")):
        return "register"
    if any(w in n for w in ("locat", "site", "coord")) or ("latitude" in cols and "longitude" in cols):
        return "locations"
    if any(w in n for w in ("financ", "summary")):
        return "financial"
    if any(w in n for w in ("adapt", "climate")):
        return "adaptation"
    return "other"


def _key_columns(columns) -> dict:
    found = {}
    for c in columns:
        a = KEY_ALIASES.get(_norm(c))
        if a and a not in found:
            found[a] = c
    return found


def _columns(rows) -> list[str]:
    out: list[str] = []
    for r in rows:
        for c in r:
            if c not in out:
                out.append(c)
    return out


def _id(v) -> str:
    return str(v if v is not None else "").strip().upper()


def _empty(v) -> bool:
    return v is None or v == ""


def _date(v) -> float:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(v).strip()[:10], fmt).timestamp()
        except ValueError:
            continue
    return 0.0


def join_tables(base: list[dict], nxt: list[dict]):
    """Left join. Returns None when the two tables share no key, else (rows, matched, duplicates, key label)."""
    bc, nc = _columns(base), _columns(nxt)
    bk, nk = _key_columns(bc), _key_columns(nc)
    alias = next((a for a in JOIN_PRIORITY if a in bk and a in nk), None)
    if not alias:
        return None
    base_key, next_key = bk[alias], nk[alias]
    date_col = next((c for c in nc if "date" in _norm(c) and re.search(r"valuation|asof|reporting|update|^date$", _norm(c))), None)
    index: dict = {}
    dups = 0
    for r in nxt:
        k = _id(r.get(next_key))
        if not k:
            continue
        if k not in index:
            index[k] = r
            continue
        dups += 1
        if date_col and _date(r.get(date_col)) > _date(index[k].get(date_col)):
            index[k] = r
    shared = set(bc)
    added = [c for c in nc if c != next_key]
    matched = 0
    out = []
    for r in base:
        hit = index.get(_id(r.get(base_key)))
        row = dict(r)
        if hit:
            matched += 1
        for c in added:
            if c in shared:
                if hit and _empty(row.get(c)):
                    row[c] = hit.get(c)
            else:
                row[c] = hit.get(c) if hit else ""
        out.append(row)
    return out, matched, dups, LABELS[alias]


def combine_files(files: list[tuple[str, list[dict]]]):
    """files = [(file name, rows)]. Returns (combined rows, steps (text), warnings (text))."""
    files = [(n, r) for n, r in files if r]
    if not files:
        return [], [], ["No rows were found in the files."]
    ranked = sorted(range(len(files)), key=lambda i: (ORDER.index(_file_kind(files[i][0], _columns(files[i][1]))), i))
    ordered = [files[i] for i in ranked]
    name0, combined = ordered[0]
    combined = [dict(r) for r in combined]
    steps = [f"Started with {name0} ({len(combined)} rows)"]
    pending = list(ordered[1:])
    progress = True
    while pending and progress:
        progress = False
        for item in list(pending):
            res = join_tables(combined, item[1])
            if not res:
                continue
            combined, matched, dups, key = res
            steps.append(f"Joined {item[0]} on {key}: {matched} of {len(combined)} rows matched"
                         + (f" ({dups} extra row(s) with an ID already used were not added; the newest, or else the first, is kept)" if dups else ""))
            pending.remove(item)
            progress = True
    warnings = [f"{n} shares no asset ID, site ID or asset name with the other files, so it was not combined and is not part of the assessment."
                for n, _ in pending]
    return combined, steps, warnings
