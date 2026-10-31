"""
Screen 2 - Compare (Stream C).

Displays:
- Top language selector (English / Hindi).
- PyDeck map with origin, candidate mandis (color-coded by A/B/C), and route paths.
- Collapsed "Data notes" box for data warnings.
- Location precision ("market" or "district centroid (approximate)") for all candidates.
- Plain-language summary box per plan above the detailed cards using app.explain.plain_grade
  and price_source_line plus plain top caveat translation.
- Collapsed expander "Detailed view (for technical users / judges)" with three detailed plan
  cards (A, B, C) featuring scenario net ranges, travel time, rain exposure, market window status,
  cost inputs, flip boundaries, commitment points, reconsider-if rules, coverage grade, robustness,
  and evidence ledger.
- Normal ABSTAIN handling.
"""
from __future__ import annotations

import pydeck as pdk
import streamlit as st

from app.contracts.inputs import PriceBand
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
    format_plain_summary,
    tag_for,
)

st.set_page_config(page_title="Compare Plans — MandiGuard", page_icon="🍅", layout="wide")

# Language toggle at top
if "lang" not in st.session_state:
    st.session_state["lang"] = "en"

col_title, col_lang = st.columns([7, 3])
with col_lang:
    lang_choice = st.radio(
        "Language / भाषा",
        options=["English", "हिंदी"],
        index=0 if st.session_state["lang"] == "en" else 1,
        horizontal=True,
    )
    new_lang = "hi" if lang_choice == "हिंदी" else "en"
    if new_lang != st.session_state["lang"]:
        st.session_state["lang"] = new_lang
        st.rerun()

lang = st.session_state["lang"]

with col_title:
    title_text = "Compare Dispatch Options" if lang == "en" else "डिस्पैच विकल्पों की तुलना (Compare Dispatch Options)"
    st.title(title_text)

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
                "coordinates": [pt["lon"], pt["lat"]],
                "name": f"{pt.get('name', pt.get('mandi_id'))} ({format_location_precision(pt.get('precision'))})",
                "color": [100, 100, 100, 200],
            }
            for pt in map_points
            if pt.get("lat") is not None and pt.get("lon") is not None
        ]
        if pt_data:
            scatter_lyr = pdk.Layer(
                "ScatterplotLayer",
                data=pt_data,
                get_position="coordinates",
                get_color="color",
                get_radius=500,
                radius_units="meters",
                radius_scale=1,
                radius_min_pixels=4,
                radius_max_pixels=12,
                pickable=True,
            )
            st.pydeck_chart(
                pdk.Deck(
                    layers=[scatter_lyr],
                    initial_view_state=pdk.ViewState(
                        latitude=pt_data[0]["coordinates"][1],
                        longitude=pt_data[0]["coordinates"][0],
                        zoom=7.5,
                    ),
                    tooltip={"text": "{name}"},
                )
            )
    st.stop()

# 2. Map View (PyDeck)
# Map candidate mandis to their Plan labels
mandi_plan_map: dict[str, str] = {}
for p in plans:
    lbl = p.get("label", "?")
    opt = p.get("option", {})
    m_id = opt.get("mandi_id")
    if m_id:
        mandi_plan_map[m_id] = lbl

scatter_data = []
# Color palette: A = Green, B = Blue, C = Orange, Candidate = Grey
COLOR_MAP = {
    "A": [34, 197, 94, 220],     # Green
    "B": [59, 130, 246, 220],    # Blue
    "C": [249, 115, 22, 220],    # Orange
    "candidate": [148, 163, 184, 180],  # Grey
    "origin": [239, 68, 68, 255],       # Red
}

req_payload = st.session_state.get("request_payload")
if req_payload and "origin" in req_payload:
    orig = req_payload["origin"]
    orig_label = req_payload.get("origin_label", "Origin")
    scatter_data.append({
        "coordinates": [orig["lon"], orig["lat"]],
        "name": f"Origin: {orig_label}",
        "color": COLOR_MAP["origin"],
        "radius": 500,
    })

for pt in map_points:
    m_id = pt.get("mandi_id", "")
    p_name = pt.get("name", m_id)
    plan_lbl = mandi_plan_map.get(m_id)
    color = COLOR_MAP.get(plan_lbl, COLOR_MAP["candidate"])
    prec_desc = format_location_precision(pt.get("precision"))
    label_desc = f"Plan {plan_lbl}: {p_name} ({prec_desc})" if plan_lbl else f"Candidate: {p_name} ({prec_desc})"
    scatter_data.append({
        "coordinates": [pt["lon"], pt["lat"]],
        "name": label_desc,
        "color": color,
        "radius": 500,
    })

# Route paths
path_data = []
for p in plans:
    lbl = p.get("label", "?")
    opt = p.get("option", {})
    r_id = opt.get("route_id")
    coords = route_paths.get(r_id, [])
    if coords:
        # Route points are [lat, lon]; PathLayer expects [lon, lat]
        path_lon_lat = [[pt[1], pt[0]] for pt in coords if len(pt) >= 2]
        path_data.append({
            "path": path_lon_lat,
            "name": f"Route for Plan {lbl}",
            "color": COLOR_MAP.get(lbl, [100, 100, 100, 180]),
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
    # Tuned ~500m real-world radius with 4px min and 12px max caps so markers
    # remain crisp dots on their exact point rather than blobs covering entire towns.
    layers.append(
        pdk.Layer(
            "ScatterplotLayer",
            data=scatter_data,
            get_position="coordinates",
            get_color="color",
            get_radius="radius",
            radius_units="meters",
            radius_scale=1,
            radius_min_pixels=4,
            radius_max_pixels=12,
            pickable=True,
        )
    )

center_lat = 18.52
center_lon = 73.86
computed_zoom = 7.5
if scatter_data:
    valid_coords = [d["coordinates"] for d in scatter_data if d.get("coordinates") and len(d["coordinates"]) == 2]
    if valid_coords:
        lons = [c[0] for c in valid_coords if c[0] is not None]
        lats = [c[1] for c in valid_coords if c[1] is not None]
        if lons and lats:
            min_lon, max_lon = min(lons), max(lons)
            min_lat, max_lat = min(lats), max(lats)
            center_lon = (min_lon + max_lon) / 2.0
            center_lat = (min_lat + max_lat) / 2.0
            max_span = max(max_lat - min_lat, max_lon - min_lon)
            if max_span > 6.0:
                computed_zoom = 6.0
            elif max_span > 3.5:
                computed_zoom = 6.8
            elif max_span > 2.0:
                computed_zoom = 7.5
            elif max_span > 1.0:
                computed_zoom = 8.2
            elif max_span > 0.5:
                computed_zoom = 9.0
            elif max_span > 0.2:
                computed_zoom = 10.2
            else:
                computed_zoom = 11.5

view_state = pdk.ViewState(
    latitude=center_lat,
    longitude=center_lon,
    zoom=computed_zoom,
    pitch=0,
)

st.pydeck_chart(
    pdk.Deck(
        layers=layers,
        initial_view_state=view_state,
        tooltip={"text": "{name}"},
    )
)

# Candidate locations precision list
with st.expander("Candidate Mandi Locations & Precision", expanded=False):
    for pt in map_points:
        m_id = pt.get("mandi_id")
        m_name = pt.get("name", m_id)
        prec = format_location_precision(pt.get("precision"))
        plan_tag = f"(Plan {mandi_plan_map[m_id]}) " if m_id in mandi_plan_map else ""
        st.markdown(f"- **{m_name}** {plan_tag}`{m_id}`: **{prec}**")

st.markdown("---")

# 3. Plain-language summary box per plan (above detailed cards)
st.subheader("Plan Recommendations (Summary)" if lang == "en" else "योजना सारांश (Plan Recommendations)")
cols_summary = st.columns(len(plans))

for idx, plan in enumerate(plans):
    lbl = plan.get("label", "?")
    opt = plan.get("option", {})
    m_id = opt.get("mandi_id", "")
    m_name = opt.get("mandi_name", m_id or "Unknown Mandi")
    ev_ids = opt.get("evidence_ids", [])
    ev_map_plan: dict[str, dict] = {eid: api_client.get_evidence_cached(eid) for eid in ev_ids}
    ev_price_plan = next(
        (ev for ev in ev_map_plan.values() if "modal" in ev.get("label", "").lower() or "typical" in ev.get("label", "").lower()),
        None,
    )
    p_date = ev_price_plan.get("as_of", "unknown") if ev_price_plan else "unknown"
    p_val = float(ev_price_plan.get("value") or 0) if ev_price_plan else 0
    p_band = PriceBand(low=0, typical=p_val, high=0, record_date=str(p_date))

    summary_info = format_plain_summary(
        grade=grade,
        price_band=p_band,
        reasons=reasons,
        lang=lang,
        market=m_name,
    )

    with cols_summary[idx]:
        st.markdown(f"### Plan {lbl}: {m_name}")
        st.info(f"**{summary_info['grade_text']}**")
        st.markdown(f"*{summary_info['price_line']}*")
        if reasons:
            st.warning(f"⚠️ **{summary_info['caveat_text']}**")
        else:
            st.success(f"✅ **{summary_info['caveat_text']}**")

st.markdown("---")

# 4. Collapsed expander for technical users & judges
expander_compare_title = (
    "Detailed view (for technical users / judges)"
    if lang == "en"
    else "Detailed view (for technical users / judges) / विस्तृत विवरण"
)
with st.expander(expander_compare_title, expanded=False):
    # Robustness & Overall Coverage Summary
    rob_a = planset.get("robustness_a_best_in", 0)
    rob_of = planset.get("robustness_of", 9)
    st.markdown(f"**Robustness:** {ROBUSTNESS_TEMPLATE.format(k=rob_a)} *(tested across price × delay scenarios)* {tag_for(source='computed')}")
    st.markdown(f"**Coverage Grade:** `{grade}` {tag_for(source='computed')}")
    if reasons:
        st.caption("Coverage reasons: " + "; ".join(reasons))

    # Detailed Plan Cards (A, B, C)
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
        duration_h = opt.get("duration_h_nominal")
        distance_km = opt.get("distance_km")
        rain_mm = opt.get("rain_exposure_mm")
        flips = plan.get("flip", {})
        commit = plan.get("commit", {})
        reconsider = plan.get("reconsider_if", [])

        ev_time = next(
            (ev for ev in ev_map.values() if ev.get("source") == "osrm" and ev.get("unit") in ("h", "s")),
            None,
        )
        time_tag = tag_for(duration_h, ev_time.get("id") if ev_time else None, source="computed")

        # 2. Distance (OSRM)
        ev_dist = next(
            (ev for ev in ev_map.values() if ev.get("source") == "osrm" and ev.get("unit") == "km"),
            None,
        )
        dist_tag = tag_for(distance_km, ev_dist.get("id") if ev_dist else None, source="computed")

        # 3. Reported price (datagov_agmarknet modal/typical)
        ev_price = next(
            (ev for ev in ev_map.values() if "modal" in ev.get("label", "").lower() or "typical" in ev.get("label", "").lower()),
            None,
        )
        price_tag = format_kind_tag(ev_price.get("kind") if ev_price else None)
        price_date = ev_price.get("as_of") if ev_price else "unknown"

        # 4. Rain exposure (engine calculation from weather forecast samples)
        rain_tag = tag_for(rain_mm, source="computed")

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
            st.markdown(f"**Typical Case:** {format_inr(typical_net)} {tag_for(source='computed')}")
            st.markdown(f"**Scenario Range (9 tests):** {format_inr(min_net)} to {format_inr(max_net)} {tag_for(source='computed')}")

            # Transit & Timing
            st.markdown("#### Transit & Timing")
            dist_str = f"{distance_km:.0f} km" if distance_km is not None else "unavailable"
            dur_str = f"{duration_h:.1f} hrs" if duration_h is not None else "unavailable"
            st.markdown(f"**{TRAVEL_TIME_LABEL}:** {dur_str} {time_tag} ({dist_str} {dist_tag})")

            if window_st == "unknown":
                st.markdown(f"**Window Status:** {WINDOW_UNKNOWN_LABEL} {tag_for(source='computed')}")
            elif window_st == "met":
                st.markdown(f"**Window Status:** Met before closing time {tag_for(source='computed')}")
            else:
                st.markdown(f"**Window Status:** Missed (trading closed) {tag_for(source='computed')}")

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
                st.markdown(f"- Delay flip: **{d_flip:.1f}% delay** {tag_for(source='computed')}")
            elif p_flip is not None:
                st.markdown(f"- Price flip: **₹{p_flip:.0f}/qtl** {tag_for(source='computed')}")
            else:
                st.markdown(f"- {NO_FLIP_LABEL} {tag_for(source='computed')}")
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
                    st.markdown(f"- {lbl_text}: **{wp_text}** {tag_for(source='computed')}")
            else:
                st.markdown(f"- Commitment points not computed (closing time unknown) {tag_for(source='computed')}")

            # Reconsider if...
            if reconsider:
                st.markdown("#### Reconsider Plan If:")
                for c in reconsider:
                    st.markdown(f"- **{c.get('kind')}** {c.get('comparison')} {c.get('threshold')}: {c.get('rationale')} {tag_for(source='computed')}")

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

    summary_text = plan_res.get("summary")
    if summary_text:
        st.subheader("Plain-language summary (template, numbers checked)")
        st.text(summary_text)

    why_not_text = plan_res.get("why_not_text")
    if why_not_text:
        st.subheader("Why not the highest-price mandi?")
        st.text(why_not_text)
