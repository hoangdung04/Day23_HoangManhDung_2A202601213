"""Lược đồ dữ liệu và hàm tính toán chỉ số đánh giá (Metrics schema and helpers)."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any

from pydantic import BaseModel, Field


class ScenarioMetric(BaseModel):
    """Chỉ số đánh giá chi tiết cho từng kịch bản (Scenario)."""

    scenario_id: str
    success: bool
    expected_route: str
    actual_route: str | None = None
    terminal_outcome: str = "unknown"
    nodes_visited: int = 0
    retry_count: int = 0
    interrupt_count: int = 0
    approval_required: bool = False
    approval_observed: bool = False
    dead_letter_observed: bool = False
    latency_ms: int = 0
    errors: list[str] = Field(default_factory=list)


class MetricsReport(BaseModel):
    """Báo cáo tổng hợp toàn bộ các chỉ số của đợt đánh giá."""

    total_scenarios: int
    success_rate: float
    avg_nodes_visited: float
    total_retries: int
    total_interrupts: int
    resume_success: bool = False
    scenario_metrics: list[ScenarioMetric]


def metric_from_state(
    state: dict[str, Any],
    expected_route: str,
    approval_required: bool,
) -> ScenarioMetric:
    """Trích xuất và tính toán chỉ số từ trạng thái kết thúc của StateGraph.

    Quy tắc xác định thành công (Workflow Success Semantics):
    - simple: route == 'simple', terminal == 'answered', có final_answer.
    - tool: route == 'tool', terminal == 'answered', không bị dead_letter, có final_answer.
    - missing_info: route == 'missing_info', terminal == 'clarified', có pending_question.
    - risky: route == 'risky', terminal == 'answered', có approval, có final_answer.
    - error: route == 'error', kết thúc hợp lệ qua 'answered' hoặc 'dead_letter'.
    """
    events = state.get("events", []) or []
    errors = state.get("errors", []) or []
    actual_route = state.get("route")
    approval = state.get("approval")
    has_final_answer = bool(state.get("final_answer"))
    has_pending_q = bool(state.get("pending_question"))

    nodes = [event.get("node", "unknown") for event in events]
    retry_count = sum(1 for node in nodes if node == "retry")
    interrupt_count = sum(1 for node in nodes if node == "approval")
    approval_observed = approval is not None
    dead_letter_observed = "dead_letter" in nodes

    # Xác định kết quả luồng công việc cuối cùng (terminal outcome)
    if dead_letter_observed:
        terminal_outcome = "dead_letter"
    elif "clarify" in nodes:
        terminal_outcome = "clarified"
    elif "answer" in nodes:
        terminal_outcome = "answered"
    else:
        # Dự phòng cho trường hợp state fixture không có events đầy đủ
        if has_pending_q:
            terminal_outcome = "clarified"
        elif has_final_answer:
            terminal_outcome = "answered"
        else:
            terminal_outcome = "unknown"

    # Kiểm tra tính hợp lệ của workflow outcome theo từng loại route
    if actual_route != expected_route:
        success = False
    elif expected_route == "simple":
        success = terminal_outcome == "answered" and has_final_answer
    elif expected_route == "tool":
        success = terminal_outcome == "answered" and not dead_letter_observed and has_final_answer
    elif expected_route == "missing_info":
        success = terminal_outcome == "clarified" and has_pending_q
    elif expected_route == "risky":
        success = terminal_outcome == "answered" and not dead_letter_observed and has_final_answer
        if approval_required:
            success = success and approval_observed
    elif expected_route == "error":
        success = terminal_outcome in ("answered", "dead_letter") and has_final_answer
    else:
        success = (has_final_answer or has_pending_q) and not dead_letter_observed

    return ScenarioMetric(
        scenario_id=str(state.get("scenario_id", "unknown")),
        success=success,
        expected_route=expected_route,
        actual_route=actual_route,
        terminal_outcome=terminal_outcome,
        nodes_visited=len(nodes),
        retry_count=retry_count,
        interrupt_count=interrupt_count,
        approval_required=approval_required,
        approval_observed=approval_observed,
        dead_letter_observed=dead_letter_observed,
        errors=list(errors),
    )


def summarize_metrics(items: list[ScenarioMetric]) -> MetricsReport:
    """Tổng hợp danh sách ScenarioMetric thành MetricsReport tổng quan."""
    if not items:
        raise ValueError("No scenario metrics to summarize")
    return MetricsReport(
        total_scenarios=len(items),
        success_rate=sum(1 for item in items if item.success) / len(items),
        avg_nodes_visited=mean(item.nodes_visited for item in items),
        total_retries=sum(item.retry_count for item in items),
        total_interrupts=sum(item.interrupt_count for item in items),
        resume_success=False,
        scenario_metrics=items,
    )


def write_metrics(report: MetricsReport, output_path: str | Path) -> None:
    """Ghi báo cáo dữ liệu JSON ra tệp tin."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
