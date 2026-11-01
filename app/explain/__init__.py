"""app.explain - deterministic template explainer (B9b)."""
from app.explain.templates import (
    FORBIDDEN_PHRASES,
    allowed_values,
    check_numbers,
    number_tokens,
    plain_grade,
    price_source_line,
    render_diff,
    render_plan_summary,
    render_why_not,
)

__all__ = ["FORBIDDEN_PHRASES", "allowed_values", "check_numbers", "number_tokens", "plain_grade",
           "price_source_line", "render_diff",
           "render_plan_summary", "render_why_not"]
