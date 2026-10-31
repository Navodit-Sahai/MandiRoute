"""
Screen 2 - Compare (Stream C).

Displays:
- PyDeck map with origin, candidate mandis (color-coded by A/B/C), and route paths.
- Collapsed "Data notes" box for data warnings.
- Location precision ("market" or "district centroid (approximate)") for all candidates.
- Three plan cards (A, B, C) with full scenario net ranges, nominal travel time,
  rain exposure, market window status, flip boundaries, commitment points,
  reconsider-if rules, coverage grade, and robustness count.
- Evidence-driven kind tags: fetched from GET /evidence/{id} (cached).
  OSRM duration & distance show [computed]; missing evidence IDs show :red[[untagged]].
  User cost inputs show [user_input].
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
    format_kind_tag,
    format_location_precision,
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

# 5. Collapsed "Data notes" box
with st.expander("Data notes", expanded=False):
    if warnings:
        for w in warnings:
            st.markdown(f"- {w}")
    else:
        st.markdown("No data warnings reported.")

# Candidate location precision map
mandi_precision_map: dict[str, str] = {
    pt.get("mandi_id", ""): format_location_precision(pt.get("precision"))
    for pt in map_points
    if pt.get("mandi_id")
}

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
        for pt in map_points:
            p_name = pt.get("name", pt.get("mandi_id"))
            p_prec = format_location_precision(pt.get("precision"))
            st.markdown(f"- **{p_name}** (`{pt.get('mandi_id')}`): Location precision: **{p_prec}**")

        pt_data = [
            {
                "name": f"{p.get('name', p.get('mandi_id'))} ({format_location_precision(p.get('precision'))})",
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

COLOR_A = [46, 125, 50, 220]      # Green
COLOR_B = [230, 81, 0, 220]       # Orange
COLOR_C = [25, 118, 210, 220]     # Blue
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
    m_id = pt.get("mandi_id", "")
    lbl = mandi_plan_map.get(m_id)
    c = plan_colors.get(lbl, COLOR_OTHER)
    prefix = f"Plan {lbl}: " if lbl else "Candidate: "
    prec_str = format_location_precision(pt.get("precision"))
    scatter_data.append({
        "name": f"{prefix}{pt.get('name', m_id)} [{prec_str}]",
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

# 5. Candidate locations precision list
with st.expander("Candidate Mandi Locations & Precision", expanded=False):
    for pt in map_points:
        m_id = pt.get("mandi_id")
        m_name = pt.get("name", m_id)
        prec = format_location_precision(pt.get("precision"))
        plan_tag = f"(Plan {mandi_plan_map[m_id]}) " if m_id in mandi_plan_map else ""
        st.markdown(f"- **{m_name}** {plan_tag}`{m_id}`: **{prec}**")

st.markdown("---")

# 3. Robustness & Overall Coverage Summary
rob_a = planset.get("robustness_a_best_in", 0)
rob_of = planset.get("robustness_of", 9)
# Robustness has no individual evidence ledger row -> untagged
st.markdown(f"**Robustness:** {ROBUSTNESS_TEMPLATE.format(k=rob_a)} *(tested across price × delay scenarios)* {format_kind_tag(None)}")
st.markdown(f"**Coverage Grade:** `{grade}` {format_kind_tag(None)}")
if reasons:
    st.caption("Coverage reasons: " + "; ".join(reasons))

# 4. Plan Cards (A, B, C)
st.subheader("Evaluated Plan Options")
cols = st.columns(len(plans))

# User cost inputs applied
costs_input = (req_payload or {}).get("costs", {})

for idx, plan in enumerate(plans):
    lbl = plan.get("label", "?")
    opt = plan.get("option", {})
    m_id = opt.get("mandi_id", "")
    m_name = opt.get("mandi_name", m_id or "Unknown Mandi")
    role_note = plan.get("role_note", "")
    scenarios = opt.get("scenario_results", [])
    ev_ids = opt.get("evidence_ids", [])

    # Fetch cached evidence records for this plan option
    ev_map: dict[str, dict] = {eid: api_client.get_evidence_cached(eid) for eid in ev_ids}

    # Match evidence records for numbers
    # 1. Travel time (OSRM)
    ev_time = next(
        (ev for ev in ev_map.values() if ev.get("source") == "osrm" and ev.get("unit") in ("h", "s")),
        None,
    )
    time_tag = format_kind_tag(ev_time.get("kind") if ev_time else None)

    # 2. Distance (OSRM)
    ev_dist = next(
        (ev for ev in ev_map.values() if ev.get("source") == "osrm" and ev.get("unit") == "km"),
        None,
    )
    dist_tag = format_kind_tag(ev_dist.get("kind") if ev_dist else None)

    # 3. Reported price (datagov_agmarknet modal/typical)
    ev_price = next(
        (ev for ev in ev_map.values() if "modal" in ev.get("label", "").lower() or "typical" in ev.get("label", "").lower()),
        None,
    )
    price_tag = format_kind_tag(ev_price.get("kind") if ev_price else None)
    price_date = ev_price.get("as_of") if ev_price else "unknown"

    # 4. Rain exposure
    ev_rain = next(
        (ev for ev in ev_map.values() if "rain" in ev.get("label", "").lower() or "precipitation" in ev.get("label", "").lower()),
        None,
    )
    rain_tag = format_kind_tag(ev_rain.get("kind") if ev_rain else None)

    # Location precision
    prec_label = mandi_precision_map.get(m_id, "unknown")

    # Net values
    nets = [s.get("net_value") for s in scenarios if s.get("net_value") is not None]
    min_net = min(nets) if nets else None
    max_net = max(nets) if nets else None

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

    with cols[idx]:
        st.markdown(f"### Plan {lbl}: {m_name}")
        st.caption(f"Location: **{prec_label}**")
        st.info(f"**Role:** {role_note}")

        # Price reported
        if ev_price and ev_price.get("value") is not None:
            st.markdown(
                f"**{PRICE_REPORTED_TEMPLATE.format(date=price_date)}:** "
                f"₹{float(ev_price['value']):.0f}/qtl {price_tag}"
            )

        # Expected Net Value
        st.markdown("#### Expected Net Value")
        # Net values have no evidence row -> untagged
        st.markdown(f"**Typical Case:** {format_inr(typical_net)} {format_kind_tag(None)}")
        st.markdown(f"**Scenario Range (9 tests):** {format_inr(min_net)} to {format_inr(max_net)} {format_kind_tag(None)}")

        # Transit & Timing
        st.markdown("#### Transit & Timing")
        dist_str = f"{distance_km:.0f} km" if distance_km is not None else "unavailable"
        dur_str = f"{duration_h:.1f} hrs" if duration_h is not None else "unavailable"
        st.markdown(f"**{TRAVEL_TIME_LABEL}:** {dur_str} {time_tag} ({dist_str} {dist_tag})")

        if window_st == "unknown":
            st.markdown(f"**Window Status:** {WINDOW_UNKNOWN_LABEL} {format_kind_tag(None)}")
        elif window_st == "met":
            st.markdown(f"**Window Status:** Met before closing time {format_kind_tag(None)}")
        else:
            st.markdown(f"**Window Status:** Missed (trading closed) {format_kind_tag(None)}")

        # Weather / Rain
        rain_str = f"{rain_mm:.1f} mm" if rain_mm is not None else "unavailable"
        st.markdown(f"**Rain Exposure along route:** {rain_str} {rain_tag}")

        # User Cost Inputs Applied
        st.markdown("#### Cost Inputs Applied")
        t_mode = costs_input.get("transport_mode", "per_km_per_trip")
        t_rate = costs_input.get("transport_rate")
        if t_rate is not None:
            st.markdown(f"- Transport: ₹{t_rate:.1f} ({t_mode}) `[user_input]`")
        c_pct = costs_input.get("commission_pct")
        if c_pct is not None:
            st.markdown(f"- Commission: {c_pct:.1f}% `[user_input]`")
        mf_pct = costs_input.get("market_fee_pct")
        if mf_pct is not None:
            st.markdown(f"- Market fee: {mf_pct:.1f}% `[user_input]`")
        h_qtl = costs_input.get("hamali_per_qtl")
        if h_qtl is not None:
            st.markdown(f"- Hamali: ₹{h_qtl:.1f}/qtl `[user_input]`")
        l_qtl = costs_input.get("loading_per_qtl")
        if l_qtl is not None:
            st.markdown(f"- Loading: ₹{l_qtl:.1f}/qtl `[user_input]`")
        h_cost = costs_input.get("hold_cost_per_hour")
        if h_cost is not None:
            st.markdown(f"- Hold cost: ₹{h_cost:.1f}/hr `[user_input]`")

        # Flip Boundaries
        st.markdown("#### Flip Boundaries")
        d_flip = flips.get("delay_flip_pct")
        p_flip = flips.get("price_flip_per_qtl")
        flip_note = flips.get("note")
        if d_flip is not None:
            st.markdown(f"- Delay flip: **{d_flip:.1f}% delay** {format_kind_tag(None)}")
        elif p_flip is not None:
            st.markdown(f"- Price flip: **₹{p_flip:.0f}/qtl** {format_kind_tag(None)}")
        else:
            st.markdown(f"- {NO_FLIP_LABEL} {format_kind_tag(None)}")
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
                st.markdown(f"- {lbl_text}: **{wp_text}** {format_kind_tag(None)}")
        else:
            st.markdown(f"- Commitment points not computed (closing time unknown) {format_kind_tag(None)}")

        # Reconsider if...
        if reconsider:
            st.markdown("#### Reconsider Plan If:")
            for c in reconsider:
                st.markdown(f"- **{c.get('kind')}** {c.get('comparison')} {c.get('threshold')}: {c.get('rationale')} {format_kind_tag(None)}")

        # Evidence Drawer
        if ev_ids:
            with st.expander(f"Evidence Ledger ({len(ev_ids)} records)", expanded=False):
                for eid in ev_ids:
                    ev = ev_map.get(eid, {})
                    ev_kind = ev.get("kind", "")
                    ev_src = ev.get("source", "")
                    ev_val = ev.get("value")
                    ev_unit = ev.get("unit", "")
                    ev_as_of = ev.get("as_of", "")
                    st.markdown(
                        f"**`{eid}`** [{ev_kind}]: {ev_val} {ev_unit} | "
                        f"*Source:* `{ev_src}` | *As of:* `{ev_as_of}`"
                    )

st.markdown("---")

# TODO: "Why not the highest-price mandi?" - engine function not yet available
