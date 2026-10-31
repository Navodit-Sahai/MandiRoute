"""app.explain - deterministic template explainer (B9b)."""
from app.explain.templates import (
    FORBIDDEN_PHRASES,
    allowed_values,
    check_numbers,
    number_tokens,
    render_diff,
    render_plan_summary,
    render_why_not,
)

__all__ = ["FORBIDDEN_PHRASES", "allowed_values", "check_numbers", "number_tokens", "render_diff",
           "render_plan_summary", "render_why_not"]
