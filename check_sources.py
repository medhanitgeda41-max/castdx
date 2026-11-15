"""Checks the live data sources once and prints what each one answered.
Run it with check_sources.bat (or: python check_sources.py). If a line says FAIL, send me the text it printed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import climate  # noqa: E402
import meteomatics  # noqa: E402
import ocean       # noqa: E402
import trends      # noqa: E402

LAT, LON = 25.30, 55.35      # sea off Dubai, UAE


def line(ok, name, text):
    print(("OK   " if ok else "FAIL ") + name + ": " + text)


def main():
    ids = ["TS", "WS10M", "WS50M", "PS", "T2M", "PRECTOTCORR"]
    try:
        js = trends.get_json(trends.POWER_URL, {"parameters": ",".join(ids), "community": "AG", "longitude": LON, "latitude": LAT, "start": 2019, "end": 2020, "format": "JSON"})
        got = trends.parse_power(js, ids)
        for k, v in got.items():
            line(bool(v), f"NASA POWER {k}", f"{len(v)} months")
    except Exception as e:      # noqa: BLE001
        line(False, "NASA POWER", str(e))
    try:
        got, errs = ocean.fetch_point((LAT, LON), list(ocean.OC), 2019, 2020)
        for i in ocean.OC:
            s = got.get(i) or {}
            if s:
                k = sorted(s)[0]
                line(True, f"{ocean.OC[i][0]}", f"{len(s)} months, first {k} = {s[k]:.4g} {ocean.OC[i][1]}")
            else:
                line(False, f"{ocean.OC[i][0]}", errs.get(i, "no data"))
        for n in ocean.SEALEVEL_NOTE:
            print("     note: " + n)
    except Exception as e:      # noqa: BLE001
        line(False, "Extra sea data", str(e))
    try:
        P = climate.project(LAT, LON, 2040, "coast")
        for it in P["items"]:
            line(it["n"] >= 5, "Climate projection " + it["name"], f"{it['n']} of {it['n_asked']} models, change by 2040 {it['mean']:+.2f}")
    except Exception as e:      # noqa: BLE001
        line(False, "Climate projection (Open-Meteo)", str(e))
    if meteomatics.enabled():
        try:
            got, errs = meteomatics.fetch_point((LAT, LON), meteomatics.SEA_IDS, 2019, 2020)
            got2, errs2 = meteomatics.fetch_point((12.0, 38.0), meteomatics.SOIL_IDS, 2019, 2020)
            got.update(got2); errs.update(errs2)
            for i, m in meteomatics.MM.items():
                s = got.get(i) or {}
                if s:
                    k = sorted(s)[0]
                    line(True, m[0], f"{len(s)} months, first {k} = {s[k]:.4g} {m[1]}")
                else:
                    line(False, m[0], errs.get(i, "no data"))
        except Exception as e:      # noqa: BLE001
            line(False, "Meteomatics", str(e))
    else:
        print("     Meteomatics: skipped (METEOMATICS_USER and METEOMATICS_PASS are not set)")
    print("\nDone. Send me any FAIL line.")


if __name__ == "__main__":
    main()
