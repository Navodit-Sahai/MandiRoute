"""
Screen 3 - Dispatch & Monitor (Stream C).

Features:
- Plan picker (from session state or user input).
- Dispatch approve / reject controls with status badge.
- Progress buttons: Passed waypoint N, Delayed +30 min, Delayed +60 min, Arrived.
- Watchdog tick controls: tick now, tick fresh, trigger status, hysteresis.
- Re-plan diff view & approval when replan_pending exists.
- Timeline of watch_events.
- Red-bordered "What-if scenario (user-set) - not observed" box.
"""
from __future__ import annotations

import json
from typing import Any

import streamlit as st

from app.ui import api_client
from app.ui.format import (
    WHAT_IF_TAG,
    WHAT_IF_TITLE,
    format_inr,
)

st.set_page_config(page_title="Dispatch & Monitor — MandiGuard", page_icon="🍅", layout="wide")

st.title("Dispatch & Monitor")

# (a) Plan picker
default_plan_id = ""
if "plan_response" in st.session_state:
    default_plan_id = st.session_state["plan_response"].get("plan_id", "")

col_picker, _ = st.columns([2, 3])
with col_picker:
    plan_id = st.text_input("Plan ID", value=default_plan_id, placeholder="Enter 8-character Plan ID").strip()

if not plan_id:
    st.info("Please select or enter a Plan ID from the **Plan** page to monitor dispatch.")
    st.stop()

try:
    plan_data = api_client.get_plan(plan_id)
except Exception as exc:
    st.error(f"Could not load plan '{plan_id}': {exc}")
    st.stop()

status = plan_data.get("status", "unknown")
latest_version = plan_data.get("latest_version", 1)
planset = plan_data.get("planset") or {}
plans = planset.get("plans", [])
req = plan_data.get("request", {})
plan_a = next((p for p in plans if p.get("label") == "A"), None)
plan_a_name = plan_a.get("option", {}).get("mandi_name", "Unknown Mandi") if plan_a else "None"

# (b) Status badge & header
col_header1, col_header2 = st.columns([3, 1])
with col_header1:
    st.subheader(f"Plan `{plan_id}` (v{latest_version}) — Plan A: **{plan_a_name}**")
    st.caption(f"Origin: {req.get('origin_label', '')} ({req.get('origin_state', '')}) | Quantity: {req.get('quantity_qtl', '')} qtl")

with col_header2:
    status_colors = {
        "awaiting_approval": "orange",
        "monitoring": "green",
        "dispatched": "blue",
        "replan_pending": "red",
        "rejected": "gray",
        "abstained": "gray",
    }
    color = status_colors.get(status, "blue")
    st.markdown(f"### Status: :{color}[{status.upper()}]")

st.markdown("---")

# (b) Approve / Reject dispatch buttons
if status == "awaiting_approval":
    st.markdown("### Dispatch Approval")
    st.write(f"Plan A is recommended: **{plan_a_name}**. Approve to proceed with dispatch and enable live monitoring.")
    col_app, col_rej, _ = st.columns([1, 1, 4])
    with col_app:
        if st.button("Approve Dispatch", type="primary"):
            try:
                res = api_client.post_approve(plan_id, version=latest_version, decision="approve", kind="dispatch")
                st.success(f"Dispatch approved! Status: {res.get('status')}")
                st.rerun()
            except Exception as e:
                st.error(f"Approval failed: {e}")
    with col_rej:
        if st.button("Reject Dispatch"):
            try:
                res = api_client.post_approve(plan_id, version=latest_version, decision="reject", kind="dispatch")
                st.warning(f"Dispatch rejected! Status: {res.get('status')}")
                st.rerun()
            except Exception as e:
                st.error(f"Rejection failed: {e}")

# (e) Re-plan Approval when status == replan_pending
if status == "replan_pending":
    st.error("### ⚠️ Re-plan Proposed")
    st.markdown("A trigger condition fired and persisted across monitoring checks. A new plan version has been proposed:")
    try:
        diff_res = api_client.get_diff(plan_id)
        diff = diff_res.get("diff", {})
        diff_text = diff_res.get("diff_text", "")
        if diff_text:
            st.text(diff_text)

        items = diff.get("items", [])
        if items:
            st.markdown("**Changed Terms:**")
            st.table([
                {
                    "Term": it.get("term"),
                    "Old Value": it.get("old"),
                    "New Value": it.get("new"),
                    "Delta": it.get("delta"),
                    "Effect": it.get("effect"),
                }
                for it in items
            ])

        col_rep_app, col_rep_rej, _ = st.columns([1, 1, 4])
        with col_rep_app:
            if st.button("Approve Re-plan", type="primary"):
                res = api_client.post_approve(plan_id, version=diff.get("new_version", latest_version), decision="approve", kind="replan")
                st.success(f"Re-plan approved! Status: {res.get('status')}")
                st.rerun()
        with col_rep_rej:
            if st.button("Keep Current Plan"):
                res = api_client.post_approve(plan_id, version=diff.get("old_version", 1), decision="reject", kind="replan")
                st.info(f"Re-plan rejected. Keeping current plan. Status: {res.get('status')}")
                st.rerun()
    except Exception as e:
        st.error(f"Could not load plan diff: {e}")

# (c) Progress controls
if status in ("dispatched", "monitoring", "replan_pending"):
    st.markdown("### Transit Progress Updates")
    col_p1, col_p2, col_p3, col_p4 = st.columns(4)

    with col_p1:
        if st.button("Delayed +30 min"):
            try:
                res = api_client.post_progress(plan_id, {"delay": "delay:30"})
                st.success("Reported delay +30 min")
                st.rerun()
            except Exception as e:
                st.error(f"Progress update failed: {e}")

    with col_p2:
        if st.button("Delayed +60 min"):
            try:
                res = api_client.post_progress(plan_id, {"delay": "delay:60"})
                st.success("Reported delay +60 min")
                st.rerun()
            except Exception as e:
                st.error(f"Progress update failed: {e}")

    with col_p3:
        wp_val = st.number_input("Waypoint Index", min_value=0, max_value=20, value=1, step=1)
        if st.button(f"Passed Waypoint {wp_val}"):
            try:
                res = api_client.post_progress(plan_id, {"passed": f"passed:{wp_val}"})
                st.success(f"Reported passed waypoint {wp_val}")
                st.rerun()
            except Exception as e:
                st.error(f"Progress update failed: {e}")

    with col_p4:
        if st.button("Arrived at Mandi"):
            try:
                res = api_client.post_progress(plan_id, {"arrived": True})
                st.success("Reported arrival at mandi")
                st.rerun()
            except Exception as e:
                st.error(f"Progress update failed: {e}")

    st.markdown("---")

    # (d) Watchdog tick controls
    st.markdown("### Watchdog Monitoring Ticks")
    col_t1, col_t2 = st.columns(2)
    with col_t1:
        if st.button("Run watchdog tick now", type="primary"):
            with st.spinner("Ticking watchdog and evaluating triggers..."):
                try:
                    res = api_client.post_watch_tick(plan_id, fresh=0)
                    st.session_state["last_tick_res"] = res
                    st.success(f"Tick completed: {res.get('decision')}")
                    st.rerun()
                except Exception as e:
                    st.error(f"Tick failed: {e}")

    with col_t2:
        if st.button("Run tick with fresh data"):
            with st.spinner("Fetching fresh data and ticking watchdog..."):
                try:
                    res = api_client.post_watch_tick(plan_id, fresh=1)
                    st.session_state["last_tick_res"] = res
                    st.success(f"Fresh tick completed: {res.get('decision')}")
                    st.rerun()
                except Exception as e:
                    st.error(f"Fresh tick failed: {e}")

    if "last_tick_res" in st.session_state:
        tres = st.session_state["last_tick_res"]
        st.info(f"**Last Decision:** {tres.get('decision')}")
        st.write(f"- Fired triggers: `{tres.get('triggers', [])}`")
        st.write(f"- Persisted triggers: `{tres.get('persisted', [])}`")
        if tres.get("diff_text"):
            st.text(tres.get("diff_text"))

st.markdown("---")

# (g) RED-BORDERED BOX: What-if scenario (user-set) - not observed
st.markdown(
    f"""
    <div style="border: 2px solid #d32f2f; border-radius: 8px; padding: 16px; margin: 16px 0; background-color: #fff8f8;">
    <h3 style="color: #d32f2f; margin-top: 0; margin-bottom: 8px;">{WHAT_IF_TITLE}</h3>
    <p style="color: #666; font-size: 14px;">Simulate hypothetical changes to Plan A without altering official records. Every number is tagged <code>{WHAT_IF_TAG}</code>.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.form("whatif_form"):
    col_w1, col_w2, col_w3, col_w4 = st.columns(4)

    with col_w1:
        w_delay = st.number_input("Delay on Plan A (minutes)", min_value=0.0, max_value=600.0, value=0.0, step=15.0)

    with col_w2:
        w_price_pct = st.number_input("Price Change % at Plan A's Mandi", min_value=-80.0, max_value=80.0, value=0.0, step=5.0)

    with col_w3:
        w_rain_mm = st.number_input("Extra Rain on Plan A Route (mm)", min_value=0.0, max_value=200.0, value=0.0, step=5.0)

    with col_w4:
        w_closing = st.text_input("Closing Time Override (HH:MM)", placeholder="e.g. 13:00")

    whatif_submitted = st.form_submit_button("Compute What-If Scenario", type="primary")

if whatif_submitted:
    whatif_payload: dict[str, Any] = {
        "delay_min": float(w_delay) if w_delay > 0 else None,
        "price_delta_pct": float(w_price_pct) if w_price_pct != 0 else None,
        "rain_mm_segment": float(w_rain_mm) if w_rain_mm > 0 else None,
        "closing_time": {plan_a.get("option", {}).get("mandi_id"): w_closing.strip()} if (w_closing.strip() and plan_a) else None,
    }

    try:
        w_res = api_client.post_whatif(plan_id, whatif_payload)
        st.session_state["whatif_result"] = w_res
        st.success("What-if scenario computed successfully!")
    except Exception as e:
        st.error(f"What-if computation failed: {e}")

if "whatif_result" in st.session_state:
    w_res = st.session_state["whatif_result"]
    new_ps = w_res.get("planset") or {}
    new_plans = new_ps.get("plans", [])
    diff_text = w_res.get("diff_text", "")

    new_plan_a = next((p for p in new_plans if p.get("label") == "A"), None)
    new_winner_name = new_plan_a.get("option", {}).get("mandi_name", "Unknown") if new_plan_a else "None"

    st.markdown(f"#### What-If Outcome: **Plan A Winner is {new_winner_name}**")
    if diff_text:
        st.text(diff_text)

    # Side-by-side comparison
    col_c1, col_c2 = st.columns(2)
    with col_c1:
        st.markdown(f"**Original Plan A:** {plan_a_name}")
        if plan_a:
            opt_orig = plan_a.get("option", {})
            st.write(f"- Score: {format_inr(opt_orig.get('score'))} {WHAT_IF_TAG}")
            st.write(f"- Nominal Duration: {opt_orig.get('duration_h_nominal', 0):.1f} hrs {WHAT_IF_TAG}")
            st.write(f"- Distance: {opt_orig.get('distance_km', 0):.0f} km {WHAT_IF_TAG}")

    with col_c2:
        st.markdown(f"**What-If Plan A:** {new_winner_name}")
        if new_plan_a:
            opt_new = new_plan_a.get("option", {})
            st.write(f"- Score: {format_inr(opt_new.get('score'))} {WHAT_IF_TAG}")
            st.write(f"- Nominal Duration: {opt_new.get('duration_h_nominal', 0):.1f} hrs {WHAT_IF_TAG}")
            st.write(f"- Distance: {opt_new.get('distance_km', 0):.0f} km {WHAT_IF_TAG}")

st.markdown("---")

# (f) Timeline of watch_events
st.subheader("Watchdog Events Timeline")
try:
    events = api_client.get_events(plan_id)
    if events:
        for ev in reversed(events):
            e_id = ev.get("id")
            ts = ev.get("ts", "")
            kind = ev.get("kind", "")
            det = ev.get("detail", {})
            st.markdown(f"- **`{ts}`** [{kind}]: {json.dumps(det)}")
    else:
        st.caption("No events recorded yet for this plan.")
except Exception as e:
    st.caption(f"Could not load events: {e}")
