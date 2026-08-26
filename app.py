"""
Arvuti seisundi jälgija -- ÜKS FAIL.

Käivitamine:  python app.py
See ongi kogu programm: server + veebivaade + AI-kokkuvõte kõik samas
failis. Käivitades avaneb brauser ise. Ei ole eraldi HTML-faili, mida
kogemata otse avada saaks -- kõik on siin sees.

Mida see loeb: AINULT protsessori koormust, mälu kasutust ja aku laetust
(psutil kaudu), iga 5 sekundi järel, taustal. Ei mingit heli, kaamerat,
faile ega isikuandmeid.

Esmakordsel käivitamisel paigalda sõltuvused:
    pip install fastapi uvicorn psutil anthropic
(anthropic on valikuline -- vajalik ainult siis, kui tahad AI-põhist
kokkuvõtet, mitte lihtsat malli. Vt allpool ANTHROPIC_API_KEY kommentaari.)
"""
import os
import sqlite3
import statistics
import threading
import time
import webbrowser
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
import uvicorn

try:
    import psutil
except ImportError:
    raise SystemExit("Puudub 'psutil'. Käivita: pip install psutil")


# ---------------------------------------------------------------------------
# Seaded
# ---------------------------------------------------------------------------
DB_PATH = Path(__file__).parent / "data.db"
POLL_INTERVAL_SECONDS = 5
SILENT_AFTER_SECONDS = 30
PORT = 8000

RANGES = {"cpu_load_pct": (0.0, 100.0), "memory_pct": (0.0, 100.0), "battery_pct": (0.0, 100.0)}
THRESHOLDS = {"cpu_load_pct": (0.0, 85.0), "memory_pct": (0.0, 90.0), "battery_pct": (15.0, 100.0)}
LABELS_ET = {"cpu_load_pct": "protsessori koormus", "memory_pct": "mälu kasutus", "battery_pct": "aku laetus"}
UNIT = {"cpu_load_pct": "%", "memory_pct": "%", "battery_pct": "%"}


# ---------------------------------------------------------------------------
# Salvestus (SQLite, üks fail kõrval)
# ---------------------------------------------------------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
            cpu_load_pct REAL, memory_pct REAL, battery_pct REAL, charging INTEGER,
            valid INTEGER NOT NULL DEFAULT 1, invalid_reason TEXT)"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON readings(ts)")
    conn.commit()
    conn.close()


def validate(reading: dict) -> tuple[bool, Optional[str]]:
    for metric, (lo, hi) in RANGES.items():
        val = reading.get(metric)
        if val is None:
            continue
        if not (lo <= val <= hi):
            return False, f"{metric}={val} on väljaspool usutavat vahemikku [{lo}, {hi}]"
    return True, None


def insert_reading(reading: dict):
    ts = datetime.now(timezone.utc).isoformat()
    is_valid, reason = validate(reading)
    conn = get_conn()
    conn.execute(
        "INSERT INTO readings (ts, cpu_load_pct, memory_pct, battery_pct, charging, valid, invalid_reason) VALUES (?,?,?,?,?,?,?)",
        (ts, reading.get("cpu_load_pct"), reading.get("memory_pct"), reading.get("battery_pct"),
         reading.get("charging"), 1 if is_valid else 0, reason),
    )
    conn.commit()
    conn.close()


def get_latest() -> Optional[dict]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM readings ORDER BY ts DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None


def get_since(minutes: float, only_valid: bool = True) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    conn = get_conn()
    q = "SELECT * FROM readings WHERE ts >= ?"
    args = [since]
    if only_valid:
        q += " AND valid = 1"
    rows = conn.execute(q + " ORDER BY ts ASC", args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_last_seen() -> Optional[datetime]:
    conn = get_conn()
    row = conn.execute("SELECT ts FROM readings ORDER BY ts DESC LIMIT 1").fetchone()
    conn.close()
    return datetime.fromisoformat(row["ts"]) if row else None


def is_silent() -> bool:
    last_seen = get_last_seen()
    if last_seen is None:
        return True
    now = datetime.now(timezone.utc)
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    return (now - last_seen).total_seconds() > SILENT_AFTER_SECONDS


# ---------------------------------------------------------------------------
# Anomaaliad -- see otsustab, mitte AI (vt README)
# ---------------------------------------------------------------------------
def compute_stats(readings: list[dict], metric: str) -> Optional[dict]:
    values = [r[metric] for r in readings if r.get(metric) is not None]
    if not values:
        return None
    return {"avg": round(statistics.mean(values), 1), "min": round(min(values), 1),
            "max": round(max(values), 1), "count": len(values)}


def detect_anomalies(readings: list[dict]) -> list[dict]:
    events = []
    for metric, (lo, hi) in THRESHOLDS.items():
        bad = []
        for r in readings:
            val = r.get(metric)
            if val is None:
                continue
            if metric == "battery_pct" and r.get("charging"):
                continue
            if not (lo <= val <= hi):
                bad.append(val)
        if bad:
            events.append({"metric": metric, "count": len(bad), "avg_value": round(sum(bad) / len(bad), 1), "range": (lo, hi)})
    return events


# ---------------------------------------------------------------------------
# AI kokkuvõte -- ainult agregaadid saadetakse, mitte toorandmed
# ---------------------------------------------------------------------------
def build_fallback_summary(stats_by_metric: dict, events: list[dict], silent: bool, minutes: float) -> str:
    parts = []
    if silent:
        parts.append("NB: andmed ei ole viimasel ajal uuenenud.")
    if not stats_by_metric:
        return " ".join(parts) + f" Viimase {minutes:.0f} minuti kohta pole kehtivaid mõõtmisi."
    sentences = [f"{LABELS_ET[m]} oli keskmiselt {s['avg']}{UNIT[m]} (vahemik {s['min']}-{s['max']}{UNIT[m]})"
                 for m, s in stats_by_metric.items()]
    parts.append(f"Viimase {minutes:.0f} minuti jooksul: " + "; ".join(sentences) + ".")
    if events:
        for e in events:
            lo, hi = e["range"]
            parts.append(f"Ebatavaline: {LABELS_ET[e['metric']]} oli keskmiselt {e['avg_value']}{UNIT[e['metric']]}, "
                          f"väljaspool tavapärast vahemikku {lo}-{hi}{UNIT[e['metric']]} ({e['count']} mõõtmist).")
    else:
        parts.append("Midagi ebatavalist ei tuvastatud.")
    return " ".join(parts)


def generate_summary(stats_by_metric: dict, events: list[dict], silent: bool, minutes: float) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"text": build_fallback_summary(stats_by_metric, events, silent, minutes),
                "source": "fallback", "error": "ANTHROPIC_API_KEY ei ole seadistatud"}
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        lines = [f"Andmed vaikivad: {'jah' if silent else 'ei'}", "Statistika:"]
        for m, s in stats_by_metric.items():
            lines.append(f"- {LABELS_ET[m]}: keskmine {s['avg']}{UNIT[m]}, min {s['min']}, max {s['max']}, mõõtmisi {s['count']}")
        lines.append("Tuvastatud ebatavalised sündmused (ainult neid tohib mainida):")
        lines += [f"- {LABELS_ET[e['metric']]}: keskmine {e['avg_value']}{UNIT[e['metric']]}, "
                  f"tavapärane vahemik {e['range'][0]}-{e['range'][1]}, {e['count']} mõõtmist väljas"
                  for e in events] or ["- (ühtegi)"]
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"), max_tokens=300,
            system="Kirjuta 2-4 lauseline eestikeelne kokkuvõte antud statistika põhjal. Kasuta ainult antud numbreid, ära leiuta uusi fakte.",
            messages=[{"role": "user", "content": "\n".join(lines)}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        return {"text": text, "source": "llm", "error": None}
    except Exception as e:
        return {"text": build_fallback_summary(stats_by_metric, events, silent, minutes),
                "source": "fallback", "error": f"LLM kutse ebaõnnestus, kasutan malli: {e}"}


# ---------------------------------------------------------------------------
# Taustal andmete kogumine
# ---------------------------------------------------------------------------
_stop_event = threading.Event()


def collector_loop():
    while not _stop_event.is_set():
        try:
            battery = psutil.sensors_battery()
            insert_reading({
                "cpu_load_pct": round(psutil.cpu_percent(interval=1.0), 1),
                "memory_pct": round(psutil.virtual_memory().percent, 1),
                "battery_pct": round(battery.percent, 1) if battery else None,
                "charging": bool(battery.power_plugged) if battery else None,
            })
        except Exception as e:
            print(f"[kogumine] viga: {e}")
        _stop_event.wait(max(0.0, POLL_INTERVAL_SECONDS - 1.0))


# ---------------------------------------------------------------------------
# Veebivaade (HTML on siin failis sees, mitte eraldi failina)
# ---------------------------------------------------------------------------
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="et"><head><meta charset="UTF-8"><title>Arvuti seisund</title>
<style>
body{font-family:-apple-system,Arial,sans-serif;max-width:640px;margin:30px auto;padding:0 16px;color:#1a1a1a}
h1{font-size:22px}
.card{border:1px solid #ddd;border-radius:8px;padding:16px;margin-bottom:16px}
.metrics{display:flex;gap:24px;flex-wrap:wrap}
.metric{min-width:120px}
.metric .value{font-size:32px;font-weight:bold}
.metric .label{color:#666;font-size:13px}
.badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:12px}
.badge.ok{background:#d7f4dd;color:#146c2e}
.badge.silent{background:#fde2e1;color:#8a1c1c}
button{padding:8px 14px;cursor:pointer}
#summaryBox{white-space:pre-wrap;line-height:1.5}
small.note{color:#888}
</style></head><body>
<h1>Arvuti seisund — reaalajas</h1>
<p class="note" style="color:#666">Loeb ainult protsessori koormust, mälu kasutust ja aku laetust. Ei salvesta ega saada mujale midagi muud.</p>
<div class="card">
  <span id="statusBadge" class="badge ok">laadin...</span>
  <div class="metrics" style="margin-top:12px">
    <div class="metric"><div class="value" id="valCpu">–</div><div class="label">CPU koormus, %</div></div>
    <div class="metric"><div class="value" id="valMem">–</div><div class="label">mälu kasutus, %</div></div>
    <div class="metric"><div class="value" id="valBattery">–</div><div class="label">aku laetus, %</div></div>
  </div>
  <small class="note" id="lastTs"></small>
</div>
<div class="card">
  <div>Kokkuvõte viimase
    <select id="minutesSelect">
      <option value="5">5 minuti</option>
      <option value="30" selected>30 minuti</option>
      <option value="60">60 minuti</option>
    </select> kohta
    <button onclick="loadSummary()">Genereeri kokkuvõte</button>
  </div>
  <p id="summaryBox">–</p>
  <small class="note" id="summarySource"></small>
</div>
<script>
async function loadLatest(){
  try{
    const res=await fetch('/api/stats/latest');
    if(!res.ok){setStatus(true);return;}
    const d=await res.json();
    if(d.detail){setStatus(true);return;}
    document.getElementById('valCpu').textContent=d.cpu_load_pct??'–';
    document.getElementById('valMem').textContent=d.memory_pct??'–';
    document.getElementById('valBattery').textContent=d.battery_pct??'pole akut';
    document.getElementById('lastTs').textContent='viimane mõõtmine: '+d.ts;
    setStatus(d.collector_silent);
  }catch(e){setStatus(true);}
}
function setStatus(silent){
  const b=document.getElementById('statusBadge');
  if(silent){b.textContent='andmed vaikivad';b.className='badge silent';}
  else{b.textContent='jälgimine aktiivne';b.className='badge ok';}
}
async function loadSummary(){
  const minutes=document.getElementById('minutesSelect').value;
  const box=document.getElementById('summaryBox');
  const noteEl=document.getElementById('summarySource');
  box.textContent='Koostan kokkuvõtet...';noteEl.textContent='';
  try{
    const res=await fetch(`/api/summary?minutes=${minutes}`);
    if(!res.ok){box.textContent=`Viga (HTTP ${res.status}): ${await res.text()}`;return;}
    const d=await res.json();
    box.textContent=d.summary;
    let note=`allikas: ${d.summary_source}`;
    if(d.summary_note)note+=` (${d.summary_note})`;
    noteEl.textContent=note;
  }catch(e){box.textContent=`Päring ebaõnnestus: ${e}. Kas server töötab?`;}
}
loadLatest();
setInterval(loadLatest,3000);
</script></body></html>"""


# ---------------------------------------------------------------------------
# FastAPI rakendus
# ---------------------------------------------------------------------------
app = FastAPI(title="Arvuti seisundi jälgija")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    init_db()
    threading.Thread(target=collector_loop, daemon=True).start()
    yield
    _stop_event.set()


app.router.lifespan_context = _lifespan


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML


@app.get("/api/stats/latest")
def latest():
    row = get_latest()
    if not row:
        return {"detail": "Andmeid pole veel -- oota paar sekundit."}
    row["collector_silent"] = is_silent()
    return row


@app.get("/api/stats")
def stats(minutes: float = 60):
    return get_since(minutes)


@app.get("/api/status")
def status():
    return {"collector_silent": is_silent(), "silent_after_seconds": SILENT_AFTER_SECONDS}


@app.get("/api/summary")
def summary(minutes: float = 60):
    readings = get_since(minutes)
    silent = is_silent()
    stats_by_metric = {m: s for m in RANGES if (s := compute_stats(readings, m))}
    events = detect_anomalies(readings)
    result = generate_summary(stats_by_metric, events, silent, minutes)
    return {"minutes": minutes, "collector_silent": silent, "stats": stats_by_metric,
            "anomalies": events, "summary": result["text"], "summary_source": result["source"],
            "summary_note": result["error"]}


# ---------------------------------------------------------------------------
# Käivitamine: python app.py
# ---------------------------------------------------------------------------
def _open_browser_later():
    time.sleep(1.5)
    webbrowser.open(f"http://localhost:{PORT}/")


if __name__ == "__main__":
    threading.Thread(target=_open_browser_later, daemon=True).start()
    print(f"Käivitub aadressil http://localhost:{PORT}/ -- brauser peaks ise avanema.")
    print("Selle akna sulgemine peatab serveri.")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
