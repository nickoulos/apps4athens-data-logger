"""Download the GitHub Actions artifacts and merge them (plus local data) into one dataset.

Usage:  python merge.py                 # download new artifacts, merge cloud + local
        python merge.py --no-download   # merge what is already downloaded
        python merge.py --cloud-only    # ignore the local Task Scheduler data

Needs the GitHub CLI (`gh`) logged in to any account (the repo is public).
Output: merged/<source>/YYYY-MM-DD.csv.gz, one file per source and UTC day, sorted and
de-duplicated, plus merged/coverage.txt (minutes with traffic data per day).
"""
import csv
import gzip
import io
import json
import subprocess
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

REPO = "nickoulos/apps4athens-data-logger"
ROOT = Path(__file__).resolve().parent
RAW = ROOT / "cloud"
OUT = ROOT / "merged"
KEYS = {  # columns that identify one observation
    "traffic": ["id", "vehicle", "timestamp"],
    "elevators": ["fetched_at", "line", "station_no", "elevator"],
    "chargers": ["evse_uid", "status", "last_updated"],
}
SORT = {"traffic": "timestamp", "elevators": "fetched_at", "chargers": "last_updated"}


def gh(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True).stdout


def download():
    RAW.mkdir(exist_ok=True)
    page, new = 1, 0
    while True:
        res = json.loads(gh("api", f"repos/{REPO}/actions/artifacts?per_page=100&page={page}"))
        arts = res.get("artifacts", [])
        for a in arts:
            dest = RAW / a["name"]
            if a["expired"] or dest.exists():
                continue
            blob = gh("api", f"repos/{REPO}/actions/artifacts/{a['id']}/zip")
            dest.mkdir()
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                z.extractall(dest)
            new += 1
        if len(arts) < 100:
            break
        page += 1
    print(f"downloaded {new} new artifacts into {RAW}")


def read_gz(path):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    # appended gzip members each restart with a header only when the file was new;
    # drop any repeated header rows defensively
    header = rows[0]
    return header, [r for r in rows[1:] if r != header]


def merge(include_local):
    roots = [p for p in RAW.glob("*")] if RAW.exists() else []
    if include_local and (ROOT / "data").exists():
        roots.append(ROOT / "data")
    for source, key in KEYS.items():
        by_day = defaultdict(dict)
        header = None
        for root in roots:
            for f in (root / source).glob("*.csv.gz"):
                h, rows = read_gz(f)
                header = header or h
                idx = [h.index(k) for k in key]
                for r in rows:
                    by_day[f.name][tuple(r[i] for i in idx)] = r
        if not header:
            continue
        (OUT / source).mkdir(parents=True, exist_ok=True)
        si = header.index(SORT[source])
        for day, rows in sorted(by_day.items()):
            with gzip.open(OUT / source / day, "wt", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(header)
                w.writerows(sorted(rows.values(), key=lambda r: r[si]))
        print(f"{source}: {sum(len(v) for v in by_day.values())} rows in {len(by_day)} days")
    # coverage: distinct minutes with at least one traffic observation
    lines = []
    for f in sorted((OUT / "traffic").glob("*.csv.gz")):
        _, rows = read_gz(f)
        minutes = {r[2][:16] for r in rows}
        lines.append(f"{f.name[:10]}  {len(minutes):5d} min  {100 * len(minutes) / 1440:5.1f}%")
    (OUT / "coverage.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    if "--no-download" not in sys.argv:
        download()
    merge(include_local="--cloud-only" not in sys.argv)
