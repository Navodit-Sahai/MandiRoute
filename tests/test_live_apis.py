"""
Live API reachability and data contract verification test.
Tests each individual external data provider:
1. data.gov.in (Agmarknet daily mandi records)
2. Open-Meteo forecast (temperature & precipitation)
3. Open-Meteo flood (river discharge proxy)
4. OSRM Demo routing (driving geometry, distance, duration)
5. OSRM Demo table (travel time matrix)
6. Open-Meteo geocoding
7. Nominatim geocoding
"""
from __future__ import annotations

import os
import pytest
import requests
from app.config import load_env, datagov_key

load_env()

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1",
    reason="Live network tests only run when RUN_LIVE_TESTS=1",
)

def test_open_meteo_forecast():
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": 18.5204,
        "longitude": 73.8567,
        "hourly": "temperature_2m,precipitation",
        "forecast_days": 3,
        "timezone": "auto",
    }
    r = requests.get(url, params=params, timeout=10)
    assert r.status_code == 200, f"Open-Meteo status {r.status_code}: {r.text}"
    data = r.json()
    assert "hourly" in data
    assert "temperature_2m" in data["hourly"]
    assert "precipitation" in data["hourly"]
    assert len(data["hourly"]["temperature_2m"]) > 0
    print(f"Open-Meteo forecast: PASS ({len(data['hourly']['temperature_2m'])} hourly samples)")

def test_open_meteo_flood():
    url = "https://flood-api.open-meteo.com/v1/flood"
    params = {
        "latitude": 18.5204,
        "longitude": 73.8567,
        "daily": "river_discharge",
        "forecast_days": 3,
    }
    r = requests.get(url, params=params, timeout=10)
    assert r.status_code == 200, f"Open-Meteo Flood status {r.status_code}: {r.text}"
    data = r.json()
    assert "daily" in data
    assert "river_discharge" in data["daily"]
    print(f"Open-Meteo Flood proxy: PASS (discharge: {data['daily']['river_discharge']})")

def test_osrm_route():
    # Pune to Nashik
    url = "https://router.project-osrm.org/route/v1/driving/73.8567,18.5204;73.7997,19.9975"
    params = {
        "overview": "full",
        "geometries": "geojson",
        "alternatives": "true",
    }
    headers = {"User-Agent": "MandiGuard-Test/1.0"}
    r = requests.get(url, params=params, headers=headers, timeout=15)
    assert r.status_code == 200, f"OSRM status {r.status_code}: {r.text}"
    data = r.json()
    assert data.get("code") == "Ok"
    routes = data.get("routes", [])
    assert len(routes) >= 1
    dist_km = routes[0]["distance"] / 1000.0
    dur_h = routes[0]["duration"] / 3600.0
    assert dist_km > 50.0
    assert dur_h > 1.0
    print(f"OSRM Route: PASS (routes={len(routes)}, dist={dist_km:.1f}km, dur={dur_h:.2f}h)")

def test_osrm_table():
    url = "https://router.project-osrm.org/table/v1/driving/73.8567,18.5204;73.7997,19.9975"
    headers = {"User-Agent": "MandiGuard-Test/1.0"}
    r = requests.get(url, headers=headers, timeout=15)
    assert r.status_code == 200, f"OSRM Table status {r.status_code}: {r.text}"
    data = r.json()
    assert data.get("code") == "Ok"
    assert "durations" in data
    print(f"OSRM Table: PASS (matrix={data['durations']})")

def test_open_meteo_geocoding():
    url = "https://geocoding-api.open-meteo.com/v1/search"
    params = {"name": "Pune", "count": 1, "format": "json"}
    r = requests.get(url, params=params, timeout=10)
    assert r.status_code == 200, f"Open-Meteo Geocoding status {r.status_code}: {r.text}"
    data = r.json()
    results = data.get("results", [])
    assert len(results) >= 1
    assert "latitude" in results[0] and "longitude" in results[0]
    print(f"Open-Meteo Geocoding: PASS ({results[0]['name']} at {results[0]['latitude']}, {results[0]['longitude']})")

def test_nominatim_geocoding():
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": "Pune, Maharashtra, India", "format": "json", "limit": 1}
    headers = {"User-Agent": "MandiGuard-Hackathon/1.0 (contact: test@mandiguard.org)"}
    r = requests.get(url, params=params, headers=headers, timeout=10)
    assert r.status_code == 200, f"Nominatim status {r.status_code}: {r.text}"
    data = r.json()
    assert len(data) >= 1
    assert "lat" in data[0] and "lon" in data[0]
    print(f"Nominatim Geocoding: PASS ({data[0]['display_name'][:50]}...)")

def test_datagov_agmarknet():
    key = datagov_key()
    assert key, "DATAGOV_KEY must be set in .env"
    url = "https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070"
    params = {
        "api-key": key,
        "format": "json",
        "limit": 5,
        "filters[commodity]": "Tomato",
        "filters[state]": "Maharashtra",
    }
    headers = {"User-Agent": "MandiGuard-Hackathon/1.0 (contact: test@mandiguard.org)"}
    r = requests.get(url, params=params, headers=headers, timeout=15)
    print(f"data.gov.in status: {r.status_code}")
    if r.status_code == 403:
        print(f"data.gov.in 403 response: {r.text}")
    assert r.status_code == 200, f"data.gov.in returned HTTP {r.status_code}: {r.text}"
    data = r.json()
    assert "records" in data
    records = data["records"]
    assert len(records) > 0
    sample = records[0]
    for required in ("state", "district", "market", "commodity", "arrival_date", "modal_price"):
        assert required in sample, f"Missing field {required} in record"
    print(f"data.gov.in Agmarknet: PASS (found {len(records)} records, newest arrival_date={sample['arrival_date']})")

if __name__ == "__main__":
    test_open_meteo_forecast()
    test_open_meteo_flood()
    test_osrm_route()
    test_osrm_table()
    test_open_meteo_geocoding()
    test_nominatim_geocoding()
    test_datagov_agmarknet()
