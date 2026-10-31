"""
Screen 2 - Compare (Stream C).

Displays:
- PyDeck map with origin, candidate mandis (color-coded by A/B/C), and route paths.
- Three plan cards (A, B, C) with full scenario net ranges, nominal travel time,
  rain exposure, market window status, flip boundaries, commitment points,
  reconsider-if rules, coverage grade, and robustness count.
- Evidence drawer linking to GET /evidence/{id}.
- Normal ABSTAIN handling.
"""
from __future__ import annotations

import streamlit as st
import pydeck as pdk

from app.ui import api_client
from app.ui.format import (
    COMMIT_APPROX_TEMPLATE,
    NO_FLIP_LABEL,
    PRICE_REPORTED_TEMPLATE,
    ROBUSTNESS_TEMPLATE,
    TRAVEL_TIME_LABEL,
    WINDOW_UNKNOWN_LABEL,
    format_inr,
    unavailable_if_none,
)

st.set_page_config(page_title="Compare Plans — MandiGuard", page_icon="🍅", layout="wide")

st.title("Compare Dispatch Options")

if "plan_response" not in st.session_state:
    st.info("No plan generated yet. Please visit page **1_Plan** to submit your dispatch requirements.")
    st.stop()

plan_res = st.session_state["plan_response"]
plan_id = plan_res.get("plan_id", "unknown")
status = plan_res.get("status", "unknown")
planset = plan_res.get("planset") or {}
plans = planset.get("plans", [])
coverage = planset.get("coverage") or plan_res.get("coverage") or {}
grade = coverage.get("grade", "UNKNOWN")
reasons = coverage.get("reasons", [])
map_points = plan_res.get("map_points", [])
route_paths = plan_res.get("route_paths", {})
warnings = plan_res.get("warnings", [])

# Health banner
try:
    health = api_client.get_health()
    banner = health.get("banner")
    if banner:
        st.warning(f"⚠️ {banner}")
except Exception:
    pass

st.caption(f"Plan ID: `{plan_id}` | Status: `{status}` | Coverage Grade: `{grade}`")

if warnings:
    with st.expander("⚠️ Assembly Warnings", expanded=False):
        for w in warnings:
            st.write(f"- {w}")

# 1. Handle ABSTAIN clearly as a normal result
if status == "abstained" or grade == "ABSTAIN" or not plans:
    st.warning("### Decision: ABSTAIN")
    st.markdown("MandiGuard recommends **ABSTAINING** from dispatch under current evidence.")
    st.markdown("**Reasons for Abstaining:**")
    for r in reasons:
        st.markdown(f"- {r}")

    # Show map of candidate mandis even on ABSTAIN if available
    if map_points:
        st.subheader("Candidate Locations Considered")
        pt_data = [
            {
                "name": p.get("name", p.get("mandi_id")),
                "coordinates": [p.get("lon"), p.get("lat")],
                "color": [160, 160, 160, 200],
            }
            for p in map_points
            if p.get("lon") is not None and p.get("lat") is not None
        ]
        if pt_data:
            view_state = pdk.ViewState(
                latitude=pt_data[0]["coordinates"][1],
                longitude=pt_data[0]["coordinates"][0],
                zoom=7,
            )
            layer = pdk.Layer(
                "ScatterplotLayer",
                data=pt_data,
                get_position="coordinates",
                get_color="color",
                get_radius=8000,
                pickable=True,
            )
            st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view_state, tooltip={"text": "{name}"}))
    st.stop()

# 2. PyDeck Map: Origin, Mandis, Route Paths
st.subheader("Route & Destination Map")

# Map color palette
COLOR_A = [46, 125, 50, 220]    # Green
COLOR_B = [230, 81, 0, 220]     # Orange
COLOR_C = [25, 118, 210, 220]   # Blue
COLOR_OTHER = [150, 150, 150, 180]
COLOR_ORIGIN = [211, 47, 47, 255] # Red

plan_colors = {"A": COLOR_A, "B": COLOR_B, "C": COLOR_C}
mandi_plan_map: dict[str, str] = {}
for p in plans:
    lbl = p.get("label")
    m_id = p.get("option", {}).get("mandi_id")
    if lbl and m_id:
        mandi_plan_map[m_id] = lbl

scatter_data = []
# Origin point from request payload if available
req_payload = st.session_state.get("request_payload")
if req_payload and "origin" in req_payload:
    orig = req_payload["origin"]
    orig_label = req_payload.get("origin_label", "Origin")
    scatter_data.append({
        "name": f"Origin: {orig_label}",
        "coordinates": [orig["lon"], orig["lat"]],
        "color": COLOR_ORIGIN,
        "radius": 12000,
    })

for pt in map_points:
    m_id = pt.get("mandi_id")
    lbl = mandi_plan_map.get(m_id)
    c = plan_colors.get(lbl, COLOR_OTHER)
    prefix = f"Plan {lbl}: " if lbl else "Candidate: "
    scatter_data.append({
        "name": f"{prefix}{pt.get('name', m_id)}",
        "coordinates": [pt.get("lon"), pt.get("lat")],
        "color": c,
        "radius": 10000 if lbl else 6000,
    })

# Route paths
path_data = []
for p in plans:
    lbl = p.get("label")
    r_id = p.get("option", {}).get("route_id")
    coords_lat_lon = route_paths.get(r_id, [])
    if coords_lat_lon:
        # PyDeck PathLayer requires [lon, lat] coordinates
        lon_lat_coords = [[pt[1], pt[0]] for pt in coords_lat_lon if len(pt) >= 2]
        if lon_lat_coords:
            path_data.append({
                "name": f"Route for Plan {lbl}",
                "path": lon_lat_coords,
                "color": plan_colors.get(lbl, [100, 100, 100, 180]),
            })

layers = []
if path_data:
    layers.append(
        pdk.Layer(
            "PathLayer",
            data=path_data,
            get_path="path",
            get_color="color",
            width_min_pixels=3,
            pickable=True,
        )
    )

if scatter_data:
    layers.append(
        pdk.Layer(
            "ScatterplotLayer",
            data=scatter_data,
            get_position="coordinates",
            get_color="color",
            get_radius="radius",
            pickable=True,
        )
    )

# Compute map center
center_lat = 18.52
center_lon = 73.86
if scatter_data:
    valid_coords = [d["coordinates"] for d in scatter_data if d["coordinates"]]
    if valid_coords:
        center_lon = sum(c[0] for c in valid_coords) / len(valid_coords)
        center_lat = sum(c[1] for c in valid_coords) / len(valid_coords)

view_state = pdk.ViewState(
    latitude=center_lat,
    longitude=center_lon,
    zoom=7,
    pitch=0,
)

st.pydeck_chart(
    pdk.Deck(
        layers=layers,
        initial_view_state=view_state,
        tooltip={"text": "{name}"},
    )
)

st.markdown("---")

# 3. Robustness & Overall Coverage Summary
rob_a = planset.get("robustness_a_best_in", 0)
rob_of = planset.get("robustness_of", 9)
st.markdown(f"**Robustness:** {ROBUSTNESS_TEMPLATE.format(k=rob_a)} *(tested across price × delay scenarios)* `[computed]`")
st.markdown(f"**Coverage Grade:** `{grade}` `[computed]`")
if reasons:
    st.caption("Coverage reasons: " + "; ".join(reasons))

# 4. Plan Cards (A, B, C)
st.subheader("Evaluated Plan Options")
cols = st.columns(len(plans))

for idx, plan in enumerate(plans):
    lbl = plan.get("label", "?")
    opt = plan.get("option", {})
    m_name = opt.get("mandi_name", opt.get("mandi_id", "Unknown Mandi"))
    role_note = plan.get("role_note", "")
    scenarios = opt.get("scenario_results", [])

    # Compute net range and typical net
    nets = [s.get("net_value") for s in scenarios if s.get("net_value") is not None]
    min_net = min(nets) if nets else None
    max_net = max(nets) if nets else None

    # Typical scenario: price_level="typical", delay_level="none"
    typical_s = next(
        (s for s in scenarios if s.get("price_level") == "typical" and s.get("delay_level") == "none"),
        scenarios[0] if scenarios else {},
    )
    typical_net = typical_s.get("net_value")
    window_st = typical_s.get("window_status", "unknown")

    duration_h = opt.get("duration_h_nominal")
    distance_km = opt.get("distance_km")
    rain_mm = opt.get("rain_exposure_mm")
    flips = plan.get("flip", {})
    commit = plan.get("commit", {})
    reconsider = plan.get("reconsider_if", [])
    ev_ids = opt.get("evidence_ids", [])

    with cols[idx]:
        st.markdown(f"### Plan {lbl}: {m_name}")
        st.info(f"**Role:** {role_note}")

        # Net Value
        st.markdown("#### Expected Net Value")
        st.markdown(f"**Typical Case:** {format_inr(typical_net)} `[computed]`")
        st.markdown(f"**Scenario Range (9 tests):** {format_inr(min_net)} to {format_inr(max_net)} `[computed]`")

        # Travel & Window
        st.markdown("#### Transit & Timing")
        dist_str = f"{distance_km:.0f} km" if distance_km is not None else "unavailable"
        dur_str = f"{duration_h:.1f} hrs" if duration_h is not None else "unavailable"
        st.markdown(f"**{TRAVEL_TIME_LABEL}:** {dur_str} ({dist_str}) `[forecast]`")

        if window_st == "unknown":
            st.markdown(f"**Window Status:** {WINDOW_UNKNOWN_LABEL} `[assumption]`")
        elif window_st == "met":
            st.markdown("**Window Status:** Met before closing time `[computed]`")
        else:
            st.markdown("**Window Status:** Missed (trading closed) `[computed]`")

        # Weather / Rain
        rain_str = f"{rain_mm:.1f} mm" if rain_mm is not None else "unavailable"
        st.markdown(f"**Rain Exposure along route:** {rain_str} `[forecast]`")

        # Flip Boundaries
        st.markdown("#### Flip Boundaries")
        d_flip = flips.get("delay_flip_pct")
        p_flip = flips.get("price_flip_per_qtl")
        flip_note = flips.get("note")
        if d_flip is not None:
            st.markdown(f"- Delay flip: **{d_flip:.1f}% delay** `[computed]`")
        elif p_flip is not None:
            st.markdown(f"- Price flip: **₹{p_flip:.0f}/qtl** `[computed]`")
        else:
            st.markdown(f"- {NO_FLIP_LABEL} `[computed]`")
        if flip_note:
            st.caption(f"Note: {flip_note}")

        # Commitment Points
        st.markdown("#### Commitment Points")
        if commit.get("computed"):
            comm_note = commit.get("note", "")
            if comm_note:
                st.caption(f"Commitment: {comm_note} (approximate)")
            waypoints = commit.get("last_feasible_waypoint", {})
            for sc_name, wp in waypoints.items():
                lbl_text = COMMIT_APPROX_TEMPLATE.format(scenario=sc_name)
                wp_text = f"Waypoint {wp}" if wp is not None else "Never feasible"
                st.markdown(f"- {lbl_text}: **{wp_text}** `[computed]`")
        else:
            st.markdown(f"- Commitment points not computed (closing time unknown) `[assumption]`")

        # Reconsider if...
        if reconsider:
            st.markdown("#### Reconsider Plan If:")
            for c in reconsider:
                st.markdown(f"- **{c.get('kind')}** {c.get('comparison')} {c.get('threshold')}: {c.get('rationale')} `[computed]`")

        # Evidence Drawer
        if ev_ids:
            with st.expander(f"Evidence Ledger ({len(ev_ids)} records)", expanded=False):
                for eid in ev_ids:
                    try:
                        ev = api_client.get_evidence(eid)
                        ev_kind = ev.get("kind", "")
                        ev_src = ev.get("source", "")
                        ev_val = ev.get("value")
                        ev_unit = ev.get("unit", "")
                        ev_as_of = ev.get("as_of", "")
                        st.markdown(
                            f"**`{eid}`** [{ev_kind}]: {ev_val} {ev_unit} | "
                            f"*Source:* `{ev_src}` | *As of:* `{ev_as_of}`"
                        )
                    except Exception:
                        st.caption(f"Evidence `{eid}`")

st.markdown("---")

# TODO: "Why not the highest-price mandi?" - engine function not yet available
