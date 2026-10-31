"""
Screen 1 - Plan (Stream C).

Form for DispatchRequest inputs, source-health status strip, read-only assumptions panel,
and plan submission with closing-time re-planning.
"""
from __future__ import annotations

from datetime import datetime, time
import zoneinfo
from typing import Any

import streamlit as st

from app.ui import api_client
from app.ui.format import (
    MANDI_SOURCE_NOTE,
    format_inr,
)

IST = zoneinfo.ZoneInfo("Asia/Kolkata")

st.set_page_config(page_title="Plan Dispatch — MandiGuard", page_icon="🍅", layout="wide")

st.title("Plan Tomato Dispatch")

# 1. Health banner
try:
    health = api_client.get_health()
    banner = health.get("banner")
    if banner:
        st.warning(f"⚠️ {banner}")
except Exception as e:
    st.error(f"Cannot connect to API backend: {e}")

# 2. Source-health strip
st.markdown("### Source Status")
try:
    sources = api_client.get_sources_health()
    cols = st.columns(len(sources) if sources else 1)
    for i, src in enumerate(sources):
        with cols[i]:
            name = src.get("source", "Unknown")
            ok = src.get("ok", False)
            status_symbol = "🟢 OK" if ok else "🔴 Failed"
            kind = src.get("kind", "")
            rec_date = src.get("newest_record_date") or src.get("as_of") or "unknown"
            
            note = f" ({MANDI_SOURCE_NOTE})" if "mandi" in name.lower() or "price" in name.lower() else ""
            st.metric(
                label=f"{name.capitalize()}{note}",
                value=status_symbol,
                help=f"Kind: {kind} | As of/Date: {rec_date}",
            )
            st.caption(f"Tag: `{kind}` | Date: `{rec_date}`")
except Exception as e:
    st.info(f"Source status unavailable: {e}")

# 3. Read-only assumptions panel
with st.expander("System Assumptions & Parameters (Read-only)", expanded=False):
    try:
        assumptions = api_client.get_assumptions()
        st.json(assumptions)
    except Exception as e:
        st.caption(f"Assumptions could not be loaded: {e}")

st.markdown("---")

# 4. Dispatch Request Form
st.subheader("Dispatch Details")

with st.form("dispatch_form"):
    col1, col2, col3 = st.columns(3)

    with col1:
        origin_label = st.text_input("Origin Label", value="Pune")
        origin_lat = st.number_input("Origin Latitude", value=18.5204, format="%.4f")
        origin_lon = st.number_input("Origin Longitude", value=73.8567, format="%.4f")
        origin_state = st.text_input("Origin State", value="Maharashtra")
        quantity_qtl = st.number_input(
            "Quantity (Quintals only, 1 qtl = 100 kg)",
            min_value=0.1,
            value=40.0,
            step=1.0,
            help="Strictly in Quintals"
        )
        max_dist_km = st.number_input("Max Search Distance (km)", min_value=50.0, max_value=1000.0, value=400.0, step=25.0)

    with col2:
        now_ist = datetime.now(IST)
        dep_date = st.date_input("Preferred Departure Date", value=now_ist.date())
        dep_time = st.time_input("Preferred Departure Time", value=now_ist.time().replace(second=0, microsecond=0))
        loading_time_input = st.time_input("Loading Time", value=time(hour=max(0, now_ist.hour - 1), minute=0))

        risk_mode = st.selectbox(
            "Risk Mode",
            options=["cvar25", "mean"],
            index=0,
            help="cvar25 optimizes for worst 25% of outcomes; mean optimizes for average net value"
        )

        transport_mode = st.selectbox(
            "Transport Rate Mode",
            options=["per_km_per_trip", "fixed_per_trip", "per_qtl_per_km"],
            index=0,
        )
        transport_rate = st.number_input("Transport Rate (₹)", min_value=0.0, value=30.0, step=1.0)

    with col3:
        st.markdown("**Optional Market & Handling Deductions** (leave blank if unknown)")
        commission_pct = st.number_input("Commission (%)", min_value=0.0, max_value=100.0, value=5.0, step=0.5)
        use_commission = st.checkbox("Include Commission", value=True)

        market_fee_pct = st.number_input("Market Fee (%)", min_value=0.0, max_value=100.0, value=2.0, step=0.5)
        use_market_fee = st.checkbox("Include Market Fee", value=True)

        other_pct = st.number_input("Other Deductions (%)", min_value=0.0, max_value=100.0, value=0.0, step=0.5)
        use_other = st.checkbox("Include Other %", value=False)

        hamali_per_qtl = st.number_input("Hamali (₹ / quintal)", min_value=0.0, value=6.0, step=0.5)
        use_hamali = st.checkbox("Include Hamali", value=True)

        loading_per_qtl = st.number_input("Loading (₹ / quintal)", min_value=0.0, value=4.0, step=0.5)
        use_loading = st.checkbox("Include Loading", value=True)

        hold_cost_per_hour = st.number_input("Hold Cost (₹ / hour)", min_value=0.0, value=100.0, step=10.0)
        use_hold = st.checkbox("Include Hold Cost", value=True)

    submitted = st.form_submit_button("Generate Plan Options", type="primary")

if submitted:
    # Build timezone-aware datetimes in Asia/Kolkata
    pref_dt = datetime.combine(dep_date, dep_time).replace(tzinfo=IST)
    load_dt = datetime.combine(dep_date, loading_time_input).replace(tzinfo=IST)

    payload: dict[str, Any] = {
        "crop": "Tomato",
        "quantity_qtl": float(quantity_qtl),
        "origin": {"lat": float(origin_lat), "lon": float(origin_lon)},
        "origin_label": origin_label,
        "origin_state": origin_state,
        "vehicle": "open",  # Hardcoded per SR-1 (Ripening OFF)
        "loading_time": load_dt.isoformat(),
        "preferred_departure": pref_dt.isoformat(),
        "max_distance_km": float(max_dist_km),
        "costs": {
            "transport_mode": transport_mode,
            "transport_rate": float(transport_rate),
            "commission_pct": float(commission_pct) if use_commission else None,
            "market_fee_pct": float(market_fee_pct) if use_market_fee else None,
            "other_pct": float(other_pct) if use_other else None,
            "hamali_per_qtl": float(hamali_per_qtl) if use_hamali else None,
            "loading_per_qtl": float(loading_per_qtl) if use_loading else None,
            "hold_cost_per_hour": float(hold_cost_per_hour) if use_hold else None,
        },
        "mandi_latest_arrival": {},
        "risk_mode": risk_mode,
        "ripening_limit_pct": 25.0,
    }

    with st.spinner("Fetching routes, weather, and mandi prices... This may take up to a minute."):
        try:
            res = api_client.post_plan(payload)
            st.session_state["plan_response"] = res
            st.session_state["request_payload"] = payload
            st.success(f"Plan generated successfully! Plan ID: `{res.get('plan_id')}` (Status: {res.get('status')})")
        except Exception as e:
            st.error(f"Failed to generate plan: {e}")

# 5. Candidate Mandis & Re-plan with Closing Times
if "plan_response" in st.session_state:
    res = st.session_state["plan_response"]
    map_points = res.get("map_points", [])
    planset = res.get("planset") or {}
    plans = planset.get("plans", [])

    st.markdown("---")
    st.subheader("Candidate Mandis & Market Closing Times")
    st.markdown("Enter mandi closing times (HH:MM in 24-hour local time) to verify trading windows:")

    # Gather unique mandis from map_points or plans
    mandis_to_show = []
    seen = set()
    for pt in map_points:
        m_id = pt.get("mandi_id")
        if m_id and m_id not in seen:
            seen.add(m_id)
            mandis_to_show.append((m_id, pt.get("name", m_id)))

    # Also make sure mandis in plans are included
    for p in plans:
        opt = p.get("option", {})
        m_id = opt.get("mandi_id")
        if m_id and m_id not in seen:
            seen.add(m_id)
            mandis_to_show.append((m_id, opt.get("mandi_name", m_id)))

    if not mandis_to_show:
        st.info("No candidate mandis identified.")
    else:
        with st.form("replan_form"):
            closing_inputs: dict[str, str] = {}
            cols = st.columns(min(len(mandis_to_show), 3))
            for i, (m_id, m_name) in enumerate(mandis_to_show):
                col = cols[i % len(cols)]
                with col:
                    val = st.text_input(
                        f"{m_name} ({m_id})",
                        value="",
                        placeholder="HH:MM (e.g. 14:00)",
                        key=f"close_{m_id}",
                    )
                    if val.strip():
                        closing_inputs[m_id] = val.strip()

            replan_submitted = st.form_submit_button("Re-plan with closing times", type="primary")

        if replan_submitted:
            if "request_payload" in st.session_state:
                new_payload = dict(st.session_state["request_payload"])
                new_payload["mandi_latest_arrival"] = closing_inputs
                with st.spinner("Re-evaluating options with market closing windows..."):
                    try:
                        replan_res = api_client.post_plan(new_payload)
                        st.session_state["plan_response"] = replan_res
                        st.session_state["request_payload"] = new_payload
                        st.success(f"Re-planned successfully with closing times! (Plan ID: `{replan_res.get('plan_id')}`)")
                    except Exception as e:
                        st.error(f"Re-plan failed: {e}")

    st.markdown("👉 **Go to page `2_Compare` in the sidebar to review the route map and option comparisons.**")
