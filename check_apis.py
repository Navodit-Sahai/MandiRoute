#!/usr/bin/env python3
"""
MandiGuard V3 - API availability checker
=========================================
Run from YOUR machine/network (the sandbox I used cannot reach these hosts).

    pip install requests
    export DATAGOV_KEY=...        # free key: data.gov.in -> register -> My Account
    export TG_TOKEN=...           # BotFather token
    export TG_CHAT_ID=...         # optional: your chat id (send /start to the bot first)
    export GROQ_API_KEY=...       # optional (LLM)
    export ANTHROPIC_API_KEY=...  # optional (LLM fallback)
    export ORS_KEY=...            # optional (OpenRouteService fallback)
    python check_apis.py                 # all non-interactive checks
    python check_apis.py --telegram-wait 90   # also test button callback + live location

Statuses:  PASS | PARTIAL | FAIL | NOT TESTED (missing key / skipped)
Levels recorded per check: reachable -> fields_ok -> fresh (where applicable).
Nothing here is fabricated: every value printed comes from the live response, or the check says why not.
Writes api_report.json next to the script.
"""
import argparse, json, os, sys, time, datetime as dt

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

UA = {"User-Agent": "mandiguard-hackathon-check/0.1 (contact: set-your-email@example.com)"}
TIMEOUT = 20
RESULTS = []

# ----------------------------------------------------------------- helpers
def record(group, api, test, status, http=None, ms=None, note="", level="", p0=False, extra=None):
    RESULTS.append(dict(group=group, api=api, test=test, status=status, http=http,
                        ms=ms, note=note, level=level, p0=p0, extra=extra or {}))
    icon = {"PASS": "[PASS]", "PARTIAL": "[PART]", "FAIL": "[FAIL]", "NOT TESTED": "[SKIP]"}[status]
    print(f"{icon} {api:<26} {test:<38} http={http!s:<5} {('%dms' % ms) if ms else '':<8} {note}")

def get(url, **kw):
    """returns (response|None, ms, error_str|None). One retry on network error."""
    kw.setdefault("timeout", TIMEOUT)
    kw.setdefault("headers", UA)
    err = None
    for attempt in range(2):
        t = time.time()
        try:
            r = requests.get(url, **kw)
            return r, int((time.time() - t) * 1000), None
        except Exception as e:  # noqa
            err = f"{type(e).__name__}: {str(e)[:90]}"
            time.sleep(1)
    return None, None, err

def jget(r):
    try:
        return r.json()
    except Exception:
        return None

def parse_date(s):
    for f in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(str(s), f).date()
        except Exception:
            pass
    return None

def sleep_rate(sec=1.1):
    time.sleep(sec)   # OSRM demo + Nominatim policy: <= 1 request/second

# ----------------------------------------------------------------- 1. MANDI PRICES
MANDI_RES = "https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070"
MANDI_FIELDS = {"state", "district", "market", "commodity", "variety", "grade",
                "arrival_date", "min_price", "max_price", "modal_price"}

def check_mandi():
    key = os.getenv("DATAGOV_KEY")
    g = "1 Mandi prices"
    if not key:
        record(g, "data.gov.in Agmarknet", "ALL (DATAGOV_KEY not set)", "NOT TESTED",
               note="get free key: data.gov.in > register > My Account", p0=True)
    else:
        cases = [("tomato / Uttar Pradesh", {"filters[commodity]": "Tomato", "filters[state]": "Uttar Pradesh"}),
                 ("tomato / Maharashtra", {"filters[commodity]": "Tomato", "filters[state]": "Maharashtra"}),
                 ("onion (other commodity)", {"filters[commodity]": "Onion"}),
                 ("tomato / Karnataka (district)", {"filters[commodity]": "Tomato", "filters[state]": "Karnataka"})]
        today = dt.date.today()
        for name, flt in cases:
            r, ms, err = get(MANDI_RES, params={"api-key": key, "format": "json", "limit": 100, **flt})
            if r is None:
                record(g, "data.gov.in Agmarknet", name, "FAIL", note=err, p0=True); continue
            if r.status_code != 200:
                record(g, "data.gov.in Agmarknet", name, "FAIL", r.status_code, ms,
                       note=("WAF/blocked? " if r.status_code in (403, 429, 503) else "") + r.text[:80].replace("\n", " "), p0=True)
                continue
            j = jget(r) or {}
            recs = j.get("records") or []
            if not recs:
                record(g, "data.gov.in Agmarknet", name, "PARTIAL", 200, ms,
                       note=f"reachable but 0 records (total={j.get('total')})", level="reachable", p0=True); continue
            keys = {k.lower() for k in recs[0].keys()}
            missing = MANDI_FIELDS - keys
            dates = [d for d in (parse_date(x.get("arrival_date") or x.get("Arrival_Date")) for x in recs) if d]
            newest = max(dates) if dates else None
            age = (today - newest).days if newest else None
            markets = len({(x.get("market") or x.get("Market")) for x in recs})
            st = "PASS" if not missing and age is not None and age <= 3 else "PARTIAL"
            record(g, "data.gov.in Agmarknet", name, st, 200, ms,
                   note=f"recs={len(recs)} total={j.get('total')} markets={markets} newest={newest} age={age}d "
                        f"missing_fields={sorted(missing) or 'none'}", level="fresh" if st == "PASS" else "fields_ok" if not missing else "reachable",
                   p0=True, extra={"keys": sorted(recs[0].keys())})
        # pagination test
        r1, ms, err = get(MANDI_RES, params={"api-key": key, "format": "json", "limit": 5, "offset": 0, "filters[commodity]": "Tomato"})
        r2, _, _ = get(MANDI_RES, params={"api-key": key, "format": "json", "limit": 5, "offset": 5, "filters[commodity]": "Tomato"})
        if r1 is not None and r2 is not None and r1.ok and r2.ok:
            a, b = jget(r1) or {}, jget(r2) or {}
            same = (a.get("records") or []) == (b.get("records") or [])
            record(g, "data.gov.in Agmarknet", "pagination offset 0 vs 5", "FAIL" if same else "PASS", 200, ms,
                   note="identical pages (offset ignored?)" if same else "different pages returned", p0=False)
        else:
            record(g, "data.gov.in Agmarknet", "pagination offset 0 vs 5", "FAIL", note=err or "non-200")
    # fallbacks (reachability only; NOT live price APIs unless noted)
    for api, url, note in [
        ("CEDA Agmarknet (hist.)", "https://agmarknet.ceda.ashoka.edu.in/", "HISTORICAL, monthly refresh - NOT live"),
        ("India Data Portal (hist.)", "https://ckandev.indiadataportal.com/", "HISTORICAL 2019-2025 (host seen in search; confirm official URL)"),
        ("agmarknet.gov.in", "https://agmarknet.gov.in/", "report pages; scraping terms UNVERIFIED"),
        ("e-NAM portal", "https://enam.gov.in/web/", "dashboard arrivals/trades; no verified public API"),
    ]:
        r, ms, err = get(url)
        record(g, api, "reachability only", "PARTIAL" if r is not None and r.status_code < 400 else "FAIL",
               r.status_code if r is not None else None, ms, note=note if r is not None else err, level="reachable")

# ----------------------------------------------------------------- 2. WEATHER
ORIGIN = (26.8467, 80.9462)       # Lucknow centre (test coordinate)
ROUTE_PTS = [(26.8467, 80.9462), (26.70, 80.70), (26.4499, 80.3319)]   # toward Kanpur (test coordinates)

def check_weather():
    g = "2 Weather"
    # a) single point hourly
    r, ms, err = get("https://api.open-meteo.com/v1/forecast",
                     params={"latitude": ORIGIN[0], "longitude": ORIGIN[1], "hourly": "temperature_2m,precipitation",
                             "forecast_days": 3, "timezone": "auto"})
    if r is None or r.status_code != 200:
        record(g, "Open-Meteo forecast", "origin hourly 3d", "FAIL", r.status_code if r is not None else None, ms, err or r.text[:80], p0=True)
    else:
        h = (jget(r) or {}).get("hourly", {})
        n = len(h.get("time", []))
        ok = n >= 48 and any(v is not None for v in h.get("temperature_2m", []))
        record(g, "Open-Meteo forecast", "origin hourly 3d", "PASS" if ok else "PARTIAL", 200, ms,
               note=f"hours={n} first={h.get('time', ['?'])[0]} (check 'forecast issue time' is not exposed: label as forecast)", level="fields_ok", p0=True)
    # b) multi-point route
    r, ms, err = get("https://api.open-meteo.com/v1/forecast",
                     params={"latitude": ",".join(str(p[0]) for p in ROUTE_PTS), "longitude": ",".join(str(p[1]) for p in ROUTE_PTS),
                             "hourly": "temperature_2m,precipitation", "forecast_days": 2, "timezone": "auto"})
    if r is None or r.status_code != 200:
        record(g, "Open-Meteo forecast", "3 route points in one call", "FAIL", r.status_code if r is not None else None, ms, err or r.text[:80], p0=True)
    else:
        j = jget(r)
        n = len(j) if isinstance(j, list) else 1
        record(g, "Open-Meteo forecast", "3 route points in one call", "PASS" if n == 3 else "PARTIAL", 200, ms, note=f"locations returned={n}", level="fields_ok", p0=True)
    # c) archive (ERA5) ~10 days ago
    d1 = (dt.date.today() - dt.timedelta(days=12)).isoformat(); d2 = (dt.date.today() - dt.timedelta(days=10)).isoformat()
    r, ms, err = get("https://archive-api.open-meteo.com/v1/archive",
                     params={"latitude": ORIGIN[0], "longitude": ORIGIN[1], "start_date": d1, "end_date": d2,
                             "hourly": "temperature_2m,precipitation"})
    record(g, "Open-Meteo archive", f"ERA5 {d1}..{d2}", "PASS" if r is not None and r.status_code == 200 and (jget(r) or {}).get("hourly") else "FAIL",
           r.status_code if r is not None else None, ms, note=err or "", level="fields_ok")
    # d) historical forecast (host from memory - verify docs)
    r, ms, err = get("https://historical-forecast-api.open-meteo.com/v1/forecast",
                     params={"latitude": ORIGIN[0], "longitude": ORIGIN[1], "start_date": d1, "end_date": d2,
                             "hourly": "temperature_2m,precipitation"})
    record(g, "Open-Meteo hist-forecast", f"{d1}..{d2}", "PASS" if r is not None and r.status_code == 200 and (jget(r) or {}).get("hourly") else "FAIL",
           r.status_code if r is not None else None, ms, note=err or "host from memory; check open-meteo.com docs if FAIL", level="fields_ok")
    # e) flood API (river discharge = river-flow PROXY, never road flooding)
    r, ms, err = get("https://flood-api.open-meteo.com/v1/flood",
                     params={"latitude": ORIGIN[0], "longitude": ORIGIN[1], "daily": "river_discharge", "forecast_days": 5})
    ok = r is not None and r.status_code == 200 and (jget(r) or {}).get("daily", {}).get("river_discharge")
    record(g, "Open-Meteo Flood (GloFAS)", "river_discharge 5d", "PASS" if ok else "FAIL" if r is None or r.status_code != 200 else "PARTIAL",
           r.status_code if r is not None else None, ms, note=(err or "PROXY ONLY - not road flooding; null possible if no river in 5km"), level="fields_ok")
    # f) fallback: MET Norway (needs identifying UA; coverage/precip quality for India UNVERIFIED)
    r, ms, err = get("https://api.met.no/weatherapi/locationforecast/2.0/compact",
                     params={"lat": ORIGIN[0], "lon": ORIGIN[1]})
    ok = r is not None and r.status_code == 200
    record(g, "MET Norway (fallback)", "locationforecast compact", "PARTIAL" if ok else "FAIL",
           r.status_code if r is not None else None, ms, note=err or "terms/attribution apply; accuracy for India UNVERIFIED", level="reachable")

# ----------------------------------------------------------------- 3. ROUTING
def lonlat(p): return f"{p[1]},{p[0]}"

def osrm_suite(g, name, base):
    o, d = ROUTE_PTS[0], ROUTE_PTS[-1]
    # route + alternatives
    r, ms, err = get(f"{base}/route/v1/driving/{lonlat(o)};{lonlat(d)}", params={"alternatives": "true", "overview": "simplified", "geometries": "geojson"})
    if r is None or r.status_code != 200:
        record(g, name, "route + alternatives", "FAIL", r.status_code if r is not None else None, ms, err or r.text[:80], p0=(name == "OSRM demo"))
    else:
        j = jget(r) or {}
        rs = j.get("routes") or []
        info = [(round(x["duration"] / 3600, 2), round(x["distance"] / 1000, 1)) for x in rs]
        record(g, name, "route + alternatives", "PASS" if rs else "FAIL", 200, ms, note=f"routes={len(rs)} (hours,km)={info}", level="fields_ok", p0=(name == "OSRM demo"))
    sleep_rate()
    # table: many waypoints -> one destination in ONE call (commitment points)
    coords = ";".join(lonlat(p) for p in ROUTE_PTS)
    r, ms, err = get(f"{base}/table/v1/driving/{coords}", params={"sources": "0;1", "destinations": "2"})
    j = jget(r) if r is not None else None
    ok = r is not None and r.status_code == 200 and j and j.get("durations")
    record(g, name, "table (waypoints->mandi, 1 call)", "PASS" if ok else "FAIL", r.status_code if r is not None else None, ms,
           note=err or (f"durations={j.get('durations')}" if ok else (r.text[:80] if r is not None else "")), level="fields_ok")
    sleep_rate()
    # invalid coords: should return structured error, not hang
    r, ms, err = get(f"{base}/route/v1/driving/0,0;200,100")
    j = jget(r) if r is not None else None
    ok = r is not None and j and j.get("code") not in (None, "Ok")
    record(g, name, "invalid coordinates handled", "PASS" if ok else "PARTIAL" if r is not None else "FAIL",
           r.status_code if r is not None else None, ms, note=err or (f"code={j.get('code')}" if j else ""), level="reachable")
    sleep_rate()

def check_routing():
    g = "3 Routing"
    osrm_suite(g, "OSRM demo", "https://router.project-osrm.org")
    osrm_suite(g, "OSM-DE OSRM (fallback)", "https://routing.openstreetmap.de/routed-car")   # path from memory
    ors = os.getenv("ORS_KEY")
    if not ors:
        record(g, "OpenRouteService (fallback)", "ALL (ORS_KEY not set)", "NOT TESTED", note="optional; free key at openrouteservice.org")
    else:
        try:
            t = time.time()
            r = requests.post("https://api.openrouteservice.org/v2/directions/driving-car",
                              json={"coordinates": [[ROUTE_PTS[0][1], ROUTE_PTS[0][0]], [ROUTE_PTS[-1][1], ROUTE_PTS[-1][0]]]},
                              headers={"Authorization": ors, **UA}, timeout=TIMEOUT)
            record(g, "OpenRouteService (fallback)", "directions", "PASS" if r.status_code == 200 else "FAIL", r.status_code, int((time.time() - t) * 1000), note=r.text[:60] if r.status_code != 200 else "")
        except Exception as e:  # noqa
            record(g, "OpenRouteService (fallback)", "directions", "FAIL", note=str(e)[:90])
    note = "self-host option: docker osrm/osrm-backend + India .osm.pbf from Geofabrik (needs RAM/disk; not tested)"
    record(g, "Self-hosted OSRM", "not checked by script", "NOT TESTED", note=note)

# ----------------------------------------------------------------- 4. GEOCODING
def check_geocode():
    g = "4 Geocoding"
    qs = [("known mandi (Delhi)", "Azadpur Mandi, Delhi"), ("known mandi (Maharashtra)", "Vashi APMC, Navi Mumbai"),
          ("ambiguous name", "Krishi Mandi"), ("Hindi spelling", "आज़ादपुर मंडी दिल्ली"), ("invalid", "zzzzq nowhereville")]
    for name, q in qs:
        r, ms, err = get("https://nominatim.openstreetmap.org/search",
                         params={"q": q, "format": "jsonv2", "limit": 3, "countrycodes": "in"})
        if r is None or r.status_code != 200:
            record(g, "Nominatim", name, "FAIL", r.status_code if r is not None else None, ms, err or r.text[:60], p0=True)
        else:
            j = jget(r) or []
            if name == "invalid":
                record(g, "Nominatim", name, "PASS" if len(j) == 0 else "PARTIAL", 200, ms, note=f"results={len(j)} (0 expected)")
            else:
                top = j[0] if j else None
                record(g, "Nominatim", name, "PASS" if top else "PARTIAL", 200, ms,
                       note=(f"n={len(j)} top='{top['display_name'][:48]}' ({top['lat']},{top['lon']}) importance={top.get('importance')}" if top else "no match -> use manual coordinate table"),
                       level="fields_ok", p0=True)
        sleep_rate()
    r, ms, err = get("https://geocoding-api.open-meteo.com/v1/search", params={"name": "Azadpur", "count": 3, "country": "IN"})
    # country param may be ignored by some versions; presence of results is what matters
    ok = r is not None and r.status_code == 200 and (jget(r) or {}).get("results")
    record(g, "Open-Meteo geocoding (fb)", "Azadpur", "PARTIAL" if ok else "FAIL", r.status_code if r is not None else None, ms,
           note=err or "city/locality level only; market-level precision UNVERIFIED", level="reachable")
    r, ms, err = get("https://photon.komoot.io/api/", params={"q": "Azadpur Mandi Delhi", "limit": 3})
    record(g, "Photon (fb)", "Azadpur Mandi", "PARTIAL" if r is not None and r.status_code == 200 else "FAIL",
           r.status_code if r is not None else None, ms, note=err or "host from memory; check terms", level="reachable")
    record(g, "Manual coordinate table", "team-maintained CSV", "NOT TESTED", note="final fallback: cache verified geocodes for your ~15 demo mandis")

# ----------------------------------------------------------------- 5. TELEGRAM
def check_telegram(wait):
    g = "5 Telegram"
    tok = os.getenv("TG_TOKEN"); chat = os.getenv("TG_CHAT_ID")
    if not tok:
        record(g, "Telegram Bot API", "ALL (TG_TOKEN not set)", "NOT TESTED", note="create bot with @BotFather", p0=True); return
    base = f"https://api.telegram.org/bot{tok}"
    r, ms, err = get(base + "/getMe")
    j = jget(r) if r is not None else None
    if not (r is not None and r.status_code == 200 and j and j.get("ok")):
        record(g, "Telegram Bot API", "getMe", "FAIL", r.status_code if r is not None else None, ms, err or str(j)[:80], p0=True); return
    record(g, "Telegram Bot API", "getMe", "PASS", 200, ms, note=f"bot=@{j['result']['username']}", level="reachable", p0=True)
    r, ms, _ = get(base + "/getWebhookInfo")
    wh = ((jget(r) or {}).get("result") or {}).get("url") if r is not None else None
    record(g, "Telegram Bot API", "webhook status", "PARTIAL" if wh else "PASS", 200 if r is not None else None, ms,
           note=f"webhook set ({wh[:40]}) -> getUpdates will 409; call deleteWebhook" if wh else "no webhook (long-polling OK)")
    if not chat:
        record(g, "Telegram Bot API", "send dispatch / buttons", "NOT TESTED", note="set TG_CHAT_ID (send /start to bot, then see getUpdates)", p0=True); return
    kb = {"inline_keyboard": [[{"text": "Approve Plan A", "callback_data": "approve_A"}, {"text": "Reject", "callback_data": "reject"}]]}
    t = time.time()
    try:
        r = requests.post(base + "/sendMessage", json={"chat_id": chat, "text": "MandiGuard API check: approve this test plan?", "reply_markup": kb}, timeout=TIMEOUT)
        ms = int((time.time() - t) * 1000); j = r.json()
        record(g, "Telegram Bot API", "sendMessage + inline buttons", "PASS" if j.get("ok") else "FAIL", r.status_code, ms,
               note=("message_id=%s" % j["result"]["message_id"]) if j.get("ok") else str(j)[:80], level="fields_ok", p0=True)
    except Exception as e:  # noqa
        record(g, "Telegram Bot API", "sendMessage + inline buttons", "FAIL", note=str(e)[:90], p0=True); return
    if wait <= 0:
        record(g, "Telegram Bot API", "callback + live location", "NOT TESTED", note="re-run with --telegram-wait 90 and press the button, then share LIVE location"); return
    print(f"      ... listening {wait}s: press the button, then Attach > Location > Share Live Location (15 min+), move a little")
    end = time.time() + wait; off = None
    got = dict(callback=False, static_location=False, live_edits=0, live_period=None, text=0)
    while time.time() < end:
        try:
            r = requests.get(base + "/getUpdates", params={"timeout": 10, "offset": off,
                             "allowed_updates": json.dumps(["message", "edited_message", "callback_query"])}, timeout=25)
            j = r.json()
        except Exception:
            continue
        if not j.get("ok"):
            record(g, "Telegram Bot API", "getUpdates", "FAIL", r.status_code, note=str(j)[:80]); return
        for u in j["result"]:
            off = u["update_id"] + 1
            if "callback_query" in u:
                got["callback"] = True
                requests.post(base + "/answerCallbackQuery", json={"callback_query_id": u["callback_query"]["id"], "text": "received"}, timeout=TIMEOUT)
            m = u.get("message") or {}
            e = u.get("edited_message") or {}
            if m.get("location"):
                got["static_location"] = True; got["live_period"] = m["location"].get("live_period")
            if e.get("location"):
                got["live_edits"] += 1
            if m.get("text"): got["text"] += 1
    st_cb = "PASS" if got["callback"] else "FAIL"
    record(g, "Telegram Bot API", "callback_query (button press)", st_cb, note="received" if got["callback"] else "no button press seen in window", p0=True)
    if got["live_edits"] >= 1:
        record(g, "Telegram Bot API", "LIVE location updates", "PASS", note=f"edited_message location updates={got['live_edits']} live_period={got['live_period']}", level="fresh")
    elif got["static_location"]:
        record(g, "Telegram Bot API", "LIVE location updates", "PARTIAL", note="static location received but no live edits in window -> use check-in buttons as fallback", level="fields_ok")
    else:
        record(g, "Telegram Bot API", "LIVE location updates", "FAIL", note="no location seen -> use check-in buttons / manual ETA slip")

# ----------------------------------------------------------------- 6. LLM
def check_llm():
    g = "6 LLM"
    k = os.getenv("GROQ_API_KEY")
    if not k:
        record(g, "Groq (OpenAI-compatible)", "list models", "NOT TESTED", note="GROQ_API_KEY not set (endpoint from memory)")
    else:
        r, ms, err = get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {k}", **UA})
        ids = [m.get("id") for m in (jget(r) or {}).get("data", [])][:6] if r is not None and r.status_code == 200 else []
        record(g, "Groq (OpenAI-compatible)", "list models", "PASS" if ids else "FAIL", r.status_code if r is not None else None, ms, note=err or f"models(sample)={ids}")
    k = os.getenv("ANTHROPIC_API_KEY")
    if not k:
        record(g, "Anthropic API (fallback)", "list models", "NOT TESTED", note="ANTHROPIC_API_KEY not set")
    else:
        r, ms, err = get("https://api.anthropic.com/v1/models", headers={"x-api-key": k, "anthropic-version": "2023-06-01", **UA})
        ids = [m.get("id") for m in (jget(r) or {}).get("data", [])][:6] if r is not None and r.status_code == 200 else []
        record(g, "Anthropic API (fallback)", "list models", "PASS" if ids else "FAIL", r.status_code if r is not None else None, ms, note=err or f"models(sample)={ids}")

# ----------------------------------------------------------------- summary
def summarize():
    print("\n" + "=" * 100 + "\nSUMMARY BY GROUP (P0 = must work for the core demo)")
    groups = sorted({r["group"] for r in RESULTS})
    blockers, untested = [], []
    for g in groups:
        rs = [r for r in RESULTS if r["group"] == g]
        p0 = [r for r in rs if r["p0"]]
        ran = [r for r in p0 if r["status"] != "NOT TESTED"]
        passed = sum(r["status"] == "PASS" for r in p0)
        fb = [r for r in rs if ("fallback" in r["api"].lower() or "(fb)" in r["api"].lower()) and r["status"] in ("PASS", "PARTIAL")]
        print(f"{g:<18} P0: {passed}/{len(p0)} PASS ({len(p0)-len(ran)} not tested) | any PASS={any(r['status']=='PASS' for r in rs)} | fallback reachable={bool(fb)}")
        if p0 and not ran:
            untested.append(g)
        elif ran and passed == 0:
            blockers.append(g)
    print("\nGO/NO-GO HINTS")
    msgs = {"1": "LIVE MANDI PRICES FAILED: live-price demo not possible with this source -> decide replay-led or abstain-first scope by hour 3.",
            "2": "WEATHER FAILED: core climate logic blocked; try MET Norway fallback / different network.",
            "3": "ROUTING FAILED on demo OSRM: try OSM-DE fallback or self-host OSRM.",
            "4": "GEOCODING FAILED: use the manual coordinate table for ~15 mandis.",
            "5": "TELEGRAM FAILED: check token/network (fallback: in-app approval, no external dispatch)."}
    for g in blockers: print(" * " + msgs.get(g[0], g + " failed"))
    for g in untested: print(f" * {g}: NOT TESTED (missing key/config) - this is NOT a pass.")
    if not blockers and not untested: print(" * No P0 group fully failed. Inspect PARTIAL/FAIL lines (data age, missing fields) before committing.")
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_report.json"), "w") as f:
        json.dump(dict(run_at=dt.datetime.now().isoformat(), results=RESULTS), f, indent=2, ensure_ascii=False)
    print("\nWrote api_report.json - paste its 'results' (redact keys) back for analysis.")
    return 1 if blockers else 0

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--telegram-wait", type=int, default=0, help="seconds to listen for button callback / live location")
    ap.add_argument("--only", choices=["mandi", "weather", "routing", "geocode", "telegram", "llm"])
    a = ap.parse_args()
    steps = dict(mandi=check_mandi, weather=check_weather, routing=check_routing, geocode=check_geocode,
                 telegram=lambda: check_telegram(a.telegram_wait), llm=check_llm)
    print(f"MandiGuard API check  {dt.datetime.now().isoformat(timespec='seconds')}  (UA: {UA['User-Agent']})\n")
    for name, fn in steps.items():
        if a.only and a.only != name:
            continue
        fn()
    sys.exit(summarize())
