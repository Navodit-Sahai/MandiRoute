"""
MandiGuard Streamlit App - Entry Point (Stream C).

Navigation:
- 1_Plan: Enter dispatch requirements, check source health & assumptions, submit plan request.
- 2_Compare: View candidate mandis on map, compare Plans A/B/C, review scenarios & evidence.
- 3_Dispatch_and_Monitor: Live tracking, driver check-in, watchdog triggers, and dynamic replanning.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure repository root is on sys.path so 'app' package is always resolvable
for parent in Path(__file__).resolve().parents:
    if (parent / "app").is_dir():
        if str(parent) not in sys.path:
            sys.path.insert(0, str(parent))
        break

import streamlit as st

from app.ui import api_client

st.set_page_config(
    page_title="MandiGuard — Tomato Dispatch Decision Support",
    page_icon="🍅",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🍅 MandiGuard")
st.subheader("Tomato Dispatch Decision Support (India)")

# Health check & mode banner
try:
    health = api_client.get_health()
    banner = health.get("banner")
    mode = health.get("mode", "live")
    if banner:
        st.warning(f"⚠️ {banner}")
    st.sidebar.success(f"Backend API connected ({mode} mode)")
except Exception as e:
    st.sidebar.error("Backend API not reachable. Start FastAPI server with:\n`python -m uvicorn app.api.main:app --port 8000`")
    st.warning("⚠️ Could not connect to MandiGuard backend API at http://localhost:8000. Please start the API server.")

st.markdown("""
### Welcome to MandiGuard
MandiGuard evaluates multiple mandi destinations across price scenarios, transit delays, market closing windows, and rain exposure.

#### Workflow:
1. **Plan (`1_Plan`)**: Enter dispatch origin, tomato quantity (quintals), transport costs, and departure timing. View source health and system assumptions.
2. **Compare (`2_Compare`)**: Review the route map, candidate mandis, and Plans A, B, and C with full scenario breakdowns, flip boundaries, and evidence ledger links.
3. **Dispatch & Monitor (`3_Dispatch_and_Monitor`)**: Live monitoring, driver check-in, watchdog triggers, and dynamic replanning.

Select **1_Plan** from the sidebar to begin.
""")
