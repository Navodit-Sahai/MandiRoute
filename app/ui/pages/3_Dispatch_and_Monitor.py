"""
Screen 3 - Dispatch & Monitor (Stream C).

Live monitoring dashboard, driver check-in controls, trigger status cards,
dynamic replan diff view, human-in-the-loop approvals, and plan revision history.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure repository root is on sys.path so 'app' package is always resolvable
for parent in Path(__file__).resolve().parents:
    if (parent / 'app').is_dir():
        if str(parent) not in sys.path:
            sys.path.insert(0, str(parent))
        break


import json
from datetime import datetime
import zoneinfo
from typing import Any

import streamlit as st

from app.ui import api_client
from app.ui.format import (
    format_inr,
    format_kind_tag,
    unavailable_if_none,
)

IST = zoneinfo.ZoneInfo("Asia/Kolkata")

st.set_page_config(page_title="Dispatch & Monitor — MandiGuard", page_icon="📡", layout="wide")

st.title("Dispatch & Active Monitoring")

# 1. Health banner
try:
    health = api_client.get_health()
    banner = health.get("banner")
    if banner:
        st.warning(f"⚠️ {banner}")
except Exception as e:
    st.error(f"Cannot connect to API backend: {e}")

# 2. Plan Selection
st.markdown("### Select Monitored Dispatch Plan")

plan_list: list[dict[str, Any]] = []
try:
    plan_list = api_client.list_plans()
except Exception:
    pass

plan_options = [p["plan_id"] for p in plan_list]
selected_plan_id: str | None = None

col_sel, col_stat = st.columns([2, 1])
with col_sel:
    if plan_options:
        selected_plan_id = st.selectbox("Active Plans", options=plan_options, index=0)
    else:
        selected_plan_id = st.text_input("Enter Plan ID (hex)", value="")

if not selected_plan_id:
    st.info("No plan selected. Create a plan in 'Plan Tomato Dispatch' first.")
    st.stop()

# Fetch latest plan state from API
try:
    plan_data = api_client.get_plan(selected_plan_id)
except Exception as exc:
    st.error(f"Failed to load plan '{selected_plan_id}': {exc}")
    st.stop()

status = plan_data.get("status", "unknown")
version = plan_data.get("latest_version", 1)
planset = plan_data.get("planset") or {}
plans = planset.get("plans") or []
plan_a = plans[0] if plans else None
opt_a = plan_a.get("option", {}) if plan_a else {}

with col_stat:
    if status == "monitoring":
        st.success(f"🟢 **Status:** Active Monitoring (v{version})")
    elif status == "replan_pending":
        st.error(f"🚨 **Status:** Replan Proposed (v{version} Pending Approval)")
    elif status == "awaiting_approval":
        st.warning(f"🟡 **Status:** Awaiting Dispatch Approval (v{version})")
    else:
        st.info(f"ℹ️ **Status:** {status} (v{version})")

st.divider()

# 3. Dispatch & Check-in Controls
st.markdown("### Dispatch & Progress Controls")
c_ctl1, c_ctl2, c_ctl3, c_ctl4 = st.columns(4)

with c_ctl1:
    if status == "awaiting_approval":
        if st.button("🚀 Approve & Dispatch Truck", use_container_width=True, type="primary"):
            try:
                res = api_client.post_dispatch(selected_plan_id)
                st.success("Truck dispatched! Active monitoring initiated.")
                st.rerun()
            except Exception as e:
                st.error(f"Dispatch failed: {e}")
    else:
        st.caption("Truck dispatched. Driver check-in active.")

with c_ctl2:
    if st.button("⏱️ Report Delay (+30 min)", use_container_width=True):
        try:
            api_client.post_progress(selected_plan_id, delay_min=30.0)
            st.success("Reported +30m delay")
            st.rerun()
        except Exception as e:
            st.error(f"Failed to report delay: {e}")

with c_ctl3:
    if st.button("⏱️ Report Delay (+60 min)", use_container_width=True):
        try:
            api_client.post_progress(selected_plan_id, delay_min=60.0)
            st.success("Reported +60m delay")
            st.rerun()
        except Exception as e:
            st.error(f"Failed to report delay: {e}")

with c_ctl4:
    if st.button("🔄 Run Monitoring Tick", use_container_width=True, type="secondary"):
        with st.spinner("Executing agentic monitoring workflow..."):
            try:
                tick_res = api_client.post_watch_tick(selected_plan_id)
                if tick_res.get("propose_replan"):
                    st.warning(f"Replan proposed: {tick_res.get('decision_reason')}")
                else:
                    st.info(f"Tick completed: {tick_res.get('decision_reason')}")
                st.rerun()
            except Exception as e:
                st.error(f"Monitoring tick failed: {e}")

st.divider()

# 4. Trigger & Watchdog Status Cards
watch_info = {}
try:
    watch_resp = api_client.get_watch(selected_plan_id)
    watch_info = watch_resp.get("watch_state") or {}
except Exception:
    pass

trigger_counts = watch_info.get("trigger_counts") or {}
cooldown_until = watch_info.get("cooldown_until")
last_prog = watch_info.get("last_progress") or {}

st.markdown("### Watchdog Trigger Status")
t_col1, t_col2, t_col3, t_col4 = st.columns(4)

with t_col1:
    t1_count = trigger_counts.get("T1", 0)
    st.metric("T1: ETA Slip", f"{t1_count} / 2 ticks", delta="Active" if t1_count > 0 else "Normal")
    st.caption("Triggers when delay exceeds flip boundary")

with t_col2:
    t2_count = trigger_counts.get("T2", 0)
    st.metric("T2: Window Miss", f"{t2_count} / 2 ticks", delta="Active" if t2_count > 0 else "Normal")
    st.caption("Triggers on closing-time scenario misses")

with t_col3:
    t3_count = trigger_counts.get("T3", 0)
    st.metric("T3: Route Rain", f"{t3_count} / 2 ticks", delta="Active" if t3_count > 0 else "Normal")
    st.caption("Triggers when remaining rain increases")

with t_col4:
    st.metric("Cooldown Status", "Active" if cooldown_until else "Ready", delta=cooldown_until or "None")
    st.caption(f"Last delay: {last_prog.get('reported_delay_min', 0.0)}m")

st.divider()

# 5. Dynamic Replan Proposed Diff View (Human Approval NN-11)
diff = plan_data.get("diff")
if status == "replan_pending" and diff:
    st.warning("### ⚠️ Replan Proposed — Approval Required (NN-11)")
    st.markdown(f"**Primary Reason:** {diff.get('primary_reason', 'Data change triggered replan')}")

    diff_items = diff.get("items", [])
    if diff_items:
        cols_h = st.columns([2, 2, 2, 3])
        cols_h[0].markdown("**Parameter**")
        cols_h[1].markdown("**Current Plan**")
        cols_h[2].markdown("**Proposed Revision**")
        cols_h[3].markdown("**Observed Effect**")

        for item in diff_items:
            c = st.columns([2, 2, 2, 3])
            c[0].markdown(f"`{item.get('term')}`")
            c[1].write(unavailable_if_none(item.get("old")))
            c[2].write(unavailable_if_none(item.get("new")))
            c[3].caption(item.get("effect", ""))

    st.markdown("#### Human Approval Decision")
    st.caption("Autonomous rerouting is strictly prohibited (NN-11). Explicit human approval is required.")
    appr_col1, appr_col2, _ = st.columns([2, 2, 4])
    replan_version = plan_data.get("latest_version") or ((plan_data.get("version") or 1) + 1)
    with appr_col1:
        if st.button("✅ Approve Plan Revision", use_container_width=True, type="primary"):
            try:
                api_client.post_approval(selected_plan_id, kind="replan", decision="approve", version=replan_version)
                st.success("Plan revision approved! Now monitoring revised route.")
                st.rerun()
            except Exception as e:
                st.error(f"Approval failed: {e}")
    with appr_col2:
        if st.button("❌ Reject Revision (Keep Current)", use_container_width=True):
            try:
                api_client.post_approval(selected_plan_id, kind="replan", decision="reject", version=replan_version)
                st.info("Revision rejected. Retaining current plan.")
                st.rerun()
            except Exception as e:
                st.error(f"Rejection failed: {e}")

    st.divider()

# 6. Current Plan Details
if plan_a:
    st.markdown(f"### Current Active Recommendation (Plan A — v{version})")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Destination Mandi", opt_a.get("mandi_name", "Unknown"))
    m2.metric("Risk-Adjusted Value", format_inr(opt_a.get("score")))
    m3.metric("Route Distance", f"{opt_a.get('distance_km', 0):.1f} km")
    m4.metric("Nominal Duration", f"{opt_a.get('duration_h_nominal', 0):.1f} h")

st.divider()

# 7. Plan Version History & Audit Trail
st.markdown("### Plan Version History & Audit Trail")
versions = plan_data.get("versions", [])
if versions:
    for v_entry in versions:
        v_num = v_entry.get("version")
        v_ts = v_entry.get("created_at")
        v_planset = json.loads(v_entry.get("planset_json", "{}")) if v_entry.get("planset_json") else {}
        v_diff = json.loads(v_entry.get("diff_json", "{}")) if v_entry.get("diff_json") else None
        v_plan_a = (v_planset.get("plans") or [{}])[0].get("option", {})

        with st.expander(f"Version {v_num} — Created: {v_ts} (Destination: {v_plan_a.get('mandi_name', 'N/A')})"):
            if v_diff:
                st.markdown(f"**Trigger Reason:** {v_diff.get('primary_reason')}")
            st.json({
                "version": v_num,
                "created_at": v_ts,
                "destination": v_plan_a.get("mandi_name"),
                "score": v_plan_a.get("score"),
                "distance_km": v_plan_a.get("distance_km"),
            })
else:
    st.caption("No prior versions recorded.")

# 8. Recent Agent Execution Trace
st.markdown("### Agent Execution Trace")
try:
    traces = api_client.get_plan(selected_plan_id)  # from agent trace table
    trace_rows = api_client.get_plan_trace(selected_plan_id)
    if trace_rows:
        st.dataframe(
            [
                {
                    "Step": r.get("step"),
                    "Agent/Node": r.get("node"),
                    "Tool": r.get("tool") or "internal",
                    "Summary": r.get("result_summary"),
                    "Status": "OK" if r.get("ok") else "FAILED",
                    "Timestamp": r.get("ts"),
                }
                for r in trace_rows
            ],
            use_container_width=True,
        )
except Exception:
    st.caption("Agent trace unavailable.")

