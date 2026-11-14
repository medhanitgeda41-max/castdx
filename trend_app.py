#!/usr/bin/env python3
"""CASTD, Coastal Area and Soil Trend Detection: choose the coast section or the soil section, pick places on a map, choose measures, and see which ones are changing, whether the change is significant, the long-term outlook and what to do.

    python trend_app.py            starts http://127.0.0.1:9025 and opens it in your browser

Data: NASA POWER (power.larc.nasa.gov), 1981 to the last full year, plus optional NOAA, PSMSL and Open-Meteo sea data (ocean.py). Statistics: stats.py (regression + t-test, Mann-Kendall, Theil-Sen with 95% interval).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import mimetypes
import sys
import threading
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
sys.path.insert(0, str(BASE))

import causes       # noqa: E402
import events       # noqa: E402
import coast        # noqa: E402
import soil         # noqa: E402
import ocean        # noqa: E402
import stats        # noqa: E402
import meteomatics  # noqa: E402
import trends       # noqa: E402

DEFAULT_PORT = 9025             # changed on every new release so an older copy still running does not answer instead
GETTER = trends.get_json        # tests replace this


def _places(body: dict) -> list:
    out = []
    for p in (body.get("places") or [])[:12]:
        lat, lon = float(p["lat"]), float(p["lon"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("A place has coordinates outside the Earth.")
        out.append({"name": str(p.get("name") or f"{lat:.2f}, {lon:.2f}")[:60], "lat": round(lat, 4), "lon": round(lon, 4)})
    return out


def _period(body: dict) -> tuple:
    today = date.today()
    y0, y1 = int(body.get("y0") or trends.FIRST_YEAR), int(body.get("y1") or today.year - 1)
    m0, m1 = int(body.get("m0") or 1), int(body.get("m1") or 12)
    if not (1 <= m0 <= 12 and 1 <= m1 <= 12):
        raise ValueError("Months must be between 1 and 12.")
    if y0 < trends.FIRST_YEAR or y1 > today.year:
        raise ValueError(f"NASA POWER monthly data covers {trends.FIRST_YEAR} to {today.year}.")
    return y0, m0, y1, m1


def _causes(body: dict) -> dict:
    y0, m0, y1, m1 = _period(body)
    if (y1 * 12 + m1) - (y0 * 12 + m0) + 1 < (96 if str(body.get("mode") or "year") == "year" else 24):
        raise ValueError("Possible causes need at least 8 years (one value per year) or 24 months (every month), because two series are compared.")
    pl = _places({"places": [body.get("place") or {}]})
    if not pl:
        raise ValueError("Choose a place.")
    mode = str(body.get("mode") or "year")
    if mode not in ("year", "month"):
        raise ValueError("Choose one value per year or every month.")
    out = causes.analyse_causes(pl[0], str(body.get("target") or ""), y0, m0, y1, m1, list(body.get("months") or range(1, 13)), mode, GETTER)
    for d in out.get("drivers", []):
        d["harm"] = coast.driver_harm(d["id"], d.get("trend"), str(body.get("domain") or "coast"))
    return out


def _calendar(body: dict) -> dict:
    pl = _places({"places": [body.get("place") or {}]})
    if not pl:
        raise ValueError("Choose a place.")
    y0, m0, y1, m1 = _period(body)
    if str(body.get("domain")) == "soil":
        return soil.farm_calendar(pl[0], y0, m0, y1, m1, list(body.get("months") or range(1, 13)), body.get("limits") or {}, GETTER)
    return coast.sea_calendar(pl[0], y0, m0, y1, m1, list(body.get("months") or range(1, 13)), GETTER)


def _events(body: dict) -> dict:
    y0, _m0, y1, _m1 = _period(body)
    pl = _places({"places": [body.get("place") or {}]})
    if not pl:
        raise ValueError("Choose a place.")
    return events.find_events(str(body.get("driver") or ""), pl[0], y0, y1)


def _eonet(body: dict) -> dict:
    pl = _places({"places": [body.get("place") or {}]})
    if not pl:
        raise ValueError("Choose a place.")
    y0, y1 = int(body.get("y0") or 2000), int(body.get("y1") or date.today().year)
    if not (1990 <= y0 <= y1 <= date.today().year):
        raise ValueError("Choose years from 1990 to this year. EONET is thin before then.")
    return events.explore(pl[0], y0, y1, list(body.get("categories") or []), float(body.get("radius") or 10))


def _day(v) -> date:
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        raise ValueError("Choose a valid start and end date.")


def _run(body: dict) -> dict:
    domain = "soil" if str(body.get("domain")) == "soil" else "coast"
    DEF = trends.DEFAULTS[domain]
    if str(body.get("mode")) == "day":
        d0, d1 = _day(body.get("d0")), _day(body.get("d1"))
        if d0.year < 1981 or d1 > date.today():
            raise ValueError("NASA POWER daily data runs from 1981 up to a few days ago.")
        res = trends.run(_places(body), list(body.get("vars") or DEF), 0, 1, 0, 12, [], "day", GETTER, d0, d1)
    else:
        y0, m0, y1, m1 = _period(body)
        res = trends.run(_places(body), list(body.get("vars") or DEF), y0, m0, y1, m1, list(body.get("months") or range(1, 13)), str(body.get("mode") or "year"), GETTER)
    if res["failed"] and res["failed"] == len({trends.cell_of(p["lat"], p["lon"]) for p in res["places"]}):
        raise ValueError("NASA did not answer: " + "; ".join(res["errors"]))
    for pl in res["places"]:
        for vid, r in pl["vars"].items():
            if domain == "soil":
                r["alert"] = soil.alert_for(vid, r["trend"])
                if r["alert"] is not None:
                    r["alert"]["policy"] = coast.soil_policy(vid, r["alert"], r["trend"])
            else:
                r["alert"] = coast.alert_for(vid, r["trend"])
            r["outlook"] = coast.outlook(r["trend"])
    return res


def api_config(_q=None) -> dict:
    today = date.today()
    return {"variables": [v for v in trends.BY_ID.values() if v["id"] in trends.selectable()], "default_vars": trends.DEFAULT_SELECTION, "defaults": trends.DEFAULTS, "soil_limit_note": soil.LIMIT_NOTE, "first_year": trends.FIRST_YEAR,
            "last_year": today.year - 1, "max_year": today.year, "min_years": stats.MIN_YEARS, "causes_for": list(causes.KB), "event_guide": events.guide(), "ocean_selection": trends.OCEAN_SELECTION, "limit_note": coast.LIMIT_NOTE, "swim_tips": coast.SWIM_TIPS, "outlook_years": coast.OUTLOOK_YEARS, "meteomatics": meteomatics.enabled()}


def api_csv(body: dict, kind: str) -> bytes:
    res = _run(body)
    out = io.StringIO()
    csv.writer(out).writerows(trends.series_csv_rows(res) if kind == "series" else trends.csv_rows(res))
    return ("﻿" + out.getvalue()).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body: bytes, ctype: str, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        p = urlparse(self.path).path
        try:
            if p in ("/", "/index.html"):
                self._send(200, (BASE / "web" / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif p == "/api/config":
                self._json(api_config())
            elif p.startswith("/lib/"):
                f = (BASE / "web" / p.lstrip("/")).resolve()
                if (BASE / "web" / "lib").resolve() not in f.parents or not f.is_file():
                    self._send(404, b"not found", "text/plain")
                else:
                    self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
            else:
                self._send(404, b"not found", "text/plain")
        except Exception as e:      # noqa: BLE001
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if p == "/api/run":
                self._json(_run(body))
            elif p == "/api/causes":
                self._json(_causes(body))
            elif p == "/api/calendar":
                self._json(_calendar(body))
            elif p == "/api/eonet":
                self._json(_eonet(body))
            elif p == "/api/events":
                self._json(_events(body))
            elif p in ("/api/csv_results", "/api/csv_series"):
                kind = "series" if p.endswith("series") else "results"
                self._send(200, api_csv(body, kind), "text/csv; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain")
        except (ValueError, KeyError, TypeError) as e:
            self._json({"error": str(e) or "Check the places and choices."}, 400)
        except Exception as e:      # noqa: BLE001
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    srv = None
    import os
    hosted = bool(os.environ.get("PORT"))          # web hosts give the port in PORT
    host = "0.0.0.0" if hosted else "127.0.0.1"
    if hosted:
        a.port = int(os.environ["PORT"]); a.no_browser = True
    for port in range(a.port, a.port + (1 if hosted else 30)):
        try:
            srv = ThreadingHTTPServer((host, port), Handler)
            break
        except OSError:
            continue
    if srv is None:
        raise SystemExit("No free port found. Close the other RASD window and start again.")
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"RASD Earth Trends is running at {url}")
    if not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
