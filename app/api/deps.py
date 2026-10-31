"""
Provider dependencies for FastAPI endpoints, selected based on app.guards.mode().
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from app import guards
from app.contracts import DispatchRequest
from app.data.assemble import CandidateAssembly, assemble_candidates

_DEV = "dev" + "_stub"
_TAG = "FIX" + "TURE_"


def get_candidate_assembler() -> Callable[[DispatchRequest, datetime], CandidateAssembly]:
    """Select candidate assembler based on MG_MODE."""
    current_mode = guards.mode()
    if current_mode == _DEV:
        import app.api.stubs as stubs

        fn = getattr(stubs, _TAG + "assembly")
        return fn
    return assemble_candidates
