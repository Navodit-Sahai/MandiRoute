# MandiGuard — Stream A (data layer)
    pip install -r requirements.txt
    cp .env.example .env      # fill DATAGOV_KEY, MG_CONTACT_EMAIL (Nominatim policy)
    pytest                    # 108 mocked tests
    python scripts/build_coords.py --state Maharashtra --limit 15   # then verify rows on the map by hand
Entry points: app.data.assemble.assemble_candidates / refresh_for_monitoring, app.data.health.source_health.
Data: Open-Meteo (CC BY 4.0, non-commercial), OSRM demo server (<=1 req/s), Nominatim (<=1 req/s), data.gov.in Agmarknet daily records.
