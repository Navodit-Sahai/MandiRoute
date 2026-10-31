import requests, time
key ="579b464db66ec23bdd000001709e70713ce2410854e858f39815dafe"
url = "https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070"
base = {"api-key": key, "format": "json", "limit": 5, "filters[commodity]": "Tomato"}
for name, hdr in [("default python UA", {}), ("custom UA", {"User-Agent": "MandiGuard/0.1 (test)"}), ("curl UA", {"User-Agent": "curl/8.0"})]:
    t = time.time()
    try:
        r = requests.get(url, params=base, headers=hdr, timeout=30)
        print(name, r.status_code, round(time.time()-t, 1), "s")
    except Exception as e:
        print(name, "FAIL", type(e).__name__, round(time.time()-t, 1), "s")
