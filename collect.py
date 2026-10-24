"""Apps4Athens data logger — collects history from feeds that only expose current state.

Usage:  python collect.py traffic            # one poll (local: every 1 min via Task Scheduler)
        python collect.py elevators          # one poll (every 10 min)
        python collect.py chargers           # one poll (every 10 min)
        python collect.py loop MINUTES       # poll all three for MINUTES (traffic every minute,
                                             # elevators/chargers every 10th) — used by GitHub Actions

Output: $A4A_DATA/<source>/YYYY-MM-DD.csv.gz (appended gzip members, readable with
pandas.read_csv(..., compression="gzip")) plus a raw daily metadata snapshot.
Dedupe state lives in $A4A_STATE (defaults to the data dir). Stdlib only.
"""
import csv
import gzip
import io
import json
import os
import re
import sys
import time
import traceback
import urllib.request
import zipfile
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("A4A_DATA", ROOT / "data"))
STATE = Path(os.environ.get("A4A_STATE", DATA))
LOGS = Path(os.environ.get("A4A_LOGS", ROOT / "logs"))
UA = "apps4athens-research-logger/1.1 (+https://github.com/nickoulos/apps4athens-data-logger)"

TRAFFIC_URL = "https://loader.data.gov.gr/data/attica-region-traffic-data"
ELEVATORS_URL = "https://www.stasy.gr/elevators/"
MYFAH_BASE = "https://electrokinisi.yme.gov.gr/public/static_files/"
MYFAH_DYNAMIC = MYFAH_BASE + "GR.IDRO.dynamic.data.latest.json.zip"
MYFAH_STATIC = MYFAH_BASE + "GR.IDRO.static.data.latest.json.zip"


def now():
    return datetime.now(timezone.utc)


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def append_rows(source, header, rows):
    if not rows:
        return
    d = DATA / source
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{now():%Y-%m-%d}.csv.gz"
    buf = io.StringIO()
    w = csv.writer(buf)
    if not path.exists():
        w.writerow(header)
    w.writerows(rows)
    with gzip.open(path, "at", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())


def _state_path(source):
    return STATE / source / "_state.json"


def load_state(source):
    try:
        return json.loads(_state_path(source).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(source, state):
    p = _state_path(source)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(p)


def once_per_day(source, name):
    """True the first time it is called for (source, name) on a given UTC day."""
    marker = STATE / source / f"_snap_{name}"
    today = f"{now():%Y-%m-%d}"
    try:
        if marker.read_text() == today:
            return False
    except FileNotFoundError:
        pass
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(today)
    return True


def daily_snapshot(source, name, content: bytes):
    """Keep one raw copy per day (station metadata, coordinates, etc.)."""
    d = DATA / source / "meta"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{now():%Y-%m-%d}_{name}").write_bytes(content)


def traffic():
    raw = fetch(TRAFFIC_URL)
    if once_per_day("traffic", "stations"):
        daily_snapshot("traffic", "stations.jsonl.gz", gzip.compress(raw))
    state = load_state("traffic")
    rows = []
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        key = f"{r['id']}|{r.get('vehicle', '')}"
        if state.get(key) == r["timestamp"]:
            continue  # station hasn't reported a new measurement
        state[key] = r["timestamp"]
        rows.append([r["id"], r.get("vehicle", ""), r["timestamp"],
                     r.get("count", ""), r.get("occ", ""), r.get("speed", "")])
    append_rows("traffic", ["id", "vehicle", "timestamp", "count", "occ", "speed"], rows)
    save_state("traffic", state)
    return len(rows)


STATION_HEAD_RE = re.compile(r'(line\d+)station(\d+) jw_status(\w+)')
STATION_NAME_RE = re.compile(r'<span class="jw_station__name">(.*?)</span>', re.S)
ELEVATOR_RE = re.compile(r"<li class='jw_elevatorStatus(\w+)'>(.*?)<span", re.S)


def elevators():
    html = fetch(ELEVATORS_URL).decode("utf-8", "replace")
    ts = now().isoformat(timespec="seconds")
    rows = []
    # Each station block starts with <li class="jw_line_station ...">; split on it so a
    # station without an elevator list can never swallow its neighbour's details.
    for block in html.split('<li class="jw_line_station ')[1:]:
        head = STATION_HEAD_RE.match(block)
        name_m = STATION_NAME_RE.search(block)
        if not head or not name_m:
            continue
        line, st_no, st_status = head.groups()
        name = unescape(name_m.group(1)).strip()
        elevs = ELEVATOR_RE.findall(block)
        if not elevs:
            rows.append([ts, line, st_no, name, st_status, "", ""])
        for el_status, el_name in elevs:
            rows.append([ts, line, st_no, name, st_status, unescape(el_name).strip(), el_status])
    if not rows:
        raise RuntimeError("elevators: page parsed to 0 stations — markup may have changed")
    append_rows("elevators", ["fetched_at", "line", "station_no", "station",
                              "station_status", "elevator", "elevator_status"], rows)
    return len(rows)


def _read_zip_json(content):
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        return json.loads(z.read(z.namelist()[0]).decode("utf-8-sig"))


def chargers():
    if once_per_day("chargers", "static"):
        daily_snapshot("chargers", "static.json.zip", fetch(MYFAH_STATIC, timeout=120))
    data = _read_zip_json(fetch(MYFAH_DYNAMIC, timeout=120))
    state = load_state("chargers")
    rows = []
    for loc in data.get("Locations", []):
        for e in loc.get("evses", []):
            uid = e.get("uid")
            sig = f"{e.get('status')}|{e.get('last_updated')}"
            if not uid or state.get(uid) == sig:
                continue
            state[uid] = sig
            rows.append([loc.get("id"), loc.get("party_id"), uid,
                         e.get("status"), e.get("last_updated")])
    append_rows("chargers", ["location_id", "party_id", "evse_uid", "status", "last_updated"], rows)
    save_state("chargers", state)
    return len(rows)


JOBS = {"traffic": traffic, "elevators": elevators, "chargers": chargers}


def run(job):
    LOGS.mkdir(parents=True, exist_ok=True)
    try:
        n = JOBS[job]()
        msg = f"{now().isoformat(timespec='seconds')} {job} ok rows={n}\n"
    except Exception:
        msg = f"{now().isoformat(timespec='seconds')} {job} ERROR\n{traceback.format_exc()}"
    with open(LOGS / f"{job}.log", "a", encoding="utf-8") as f:
        f.write(msg)
    print(msg, end="", flush=True)


def loop(minutes):
    end = time.time() + minutes * 60
    while time.time() < end:
        t = now()
        run("traffic")
        if t.minute % 10 == 0:
            run("elevators")
            run("chargers")
        # sleep to the start of the next minute (+2s, after the feed's minute tick)
        time.sleep(max(1.0, 62 - time.time() % 60))


def main():
    job = sys.argv[1] if len(sys.argv) > 1 else ""
    if job == "loop":
        loop(float(sys.argv[2]) if len(sys.argv) > 2 else 60)
    elif job in JOBS:
        run(job)
    else:
        sys.exit(f"usage: collect.py {{{'|'.join(JOBS)}|loop MINUTES}}")


if __name__ == "__main__":
    main()
