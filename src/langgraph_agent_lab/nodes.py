"""Triển khai các nút trong đồ thị StateGraph (LangGraph Node Implementations)."""

from __future__ import annotations

from typing import Literal

from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field

from .llm import get_llm
from .state import AgentState, make_event


# ─── ĐẦU RA CÓ CẤU TRÚC CHO PHÂN LOẠI Ý ĐỊNH ──────────────────────────────
class ClassificationResult(BaseModel):
    """Schema đầu ra có cấu trúc cho classify_node."""

    route: Literal["simple", "tool", "missing_info", "risky", "error"] = Field(
        description="Nhánh đã phân loại: 'simple', 'tool', 'missing_info', 'risky', hoặc 'error'."
    )
    risk_level: Literal["low", "high"] = Field(
        description="Mức độ rủi ro: 'high' nếu là thao tác rủi ro cao/nhạy cảm, 'low' nếu an toàn."
    )
    reasoning: str = Field(
        default="",
        description="Giải thích ngắn gọn cho quyết định phân loại theo quy tắc ưu tiên.",
    )


CLASSIFY_PROMPT = """You are an intent classification system for an AI agent orchestration graph.
Classify the user query into exactly one of the following 5 routes:

1. 'risky': The query requests an action with side effects or destructive/sensitive operations
   (e.g., refund, delete account/data, cancel subscription/order, send email/message,
    modify account settings, execute transactions/transfers).
2. 'tool': The query requires looking up, searching, checking, or retrieving external
   information/records (e.g., lookup order status, check shipping tracking, search record,
   retrieve customer status).
3. 'missing_info': The query is too vague, ambiguous, or lacks necessary details/context
   to be handled (e.g., "Can you fix it?", "Help me with that thing").
4. 'error': The query describes or reports a system failure, timeout, crash, service outage,
   or processing error (e.g., "Timeout failure while processing request", "System crashed").
5. 'simple': General questions, FAQ, or informational queries that can be answered directly
   without tools or side effects (e.g., "How do I reset my password?", "Business hours?").

PRIORITY RULE (Crucial):
If a query contains elements of multiple routes, strictly resolve conflicts using this priority:
risky > tool > missing_info > error > simple

Examples:
- "Refund this customer for order 123" -> 'risky' (refund is a risky action)
- "Cancel my account and show status" -> 'risky'
- "Please lookup order status for order 123" -> 'tool'
- "Can you fix it?" -> 'missing_info'
- "Timeout failure while processing request" -> 'error'
- "How do I reset my password?" -> 'simple'

User Query: {query}
"""


# ─── VÍ DỤ: node hoạt động mẫu (dùng để tham khảo) ───────────────────
def intake_node(state: AgentState) -> dict:
    """Chuẩn hóa truy vấn thô đầu vào. Node này được cung cấp làm ví dụ mẫu."""
    raw = state.get("query", "")
    normalized = " ".join(raw.split())
    return {
        "query": normalized,
        "events": [make_event("intake", "completed", "query normalized")],
    }


# ─── CÁC NODE BẮT BUỘC CỦA SINH VIÊN ─────────────────────────────────
def classify_node(state: AgentState) -> dict:
    """Phân loại truy vấn người dùng bằng LLM thật sử dụng Structured Output."""
    query = state.get("query", "")

    llm = get_llm()
    structured_llm = llm.with_structured_output(ClassificationResult)

    prompt = CLASSIFY_PROMPT.format(query=query)
    result = structured_llm.invoke(prompt)

    route = result.route if result else "simple"
    risk_level = result.risk_level if result else "low"

    return {
        "route": route,
        "risk_level": risk_level,
        "events": [
            make_event(
                "classify",
                "completed",
                f"classified as {route}",
                route=route,
                risk_level=risk_level,
            )
        ],
    }


def tool_node(state: AgentState) -> dict:
    """Thực thi lệnh gọi công cụ mock.

    Mô phỏng các lỗi tạm thời cho các kịch bản nhánh 'error' để kiểm tra retry loop.
    """
    attempt = state.get("attempt", 0)
    route = state.get("route", "")
    query = state.get("query", "")
    if route == "error" and attempt < 2:
        result = f"ERROR: Tool execution failed transiently (attempt {attempt})"
    else:
        result = f"Tool execution succeeded for query: '{query}'. Status: completed successfully."
    return {
        "tool_results": [result],
        "events": [make_event("tool", "completed", "tool executed")],
    }


class EvaluationResult(BaseModel):
    """Schema đầu ra có cấu trúc cho LLM-as-judge."""

    evaluation_result: Literal["success", "needs_retry"] = Field(
        description="Quyết định đánh giá: 'success' nếu hợp lệ, 'needs_retry' nếu lỗi."
    )
    reason: str = Field(
        default="",
        description="Giải thích cho quyết định đánh giá.",
    )


EVALUATE_PROMPT = """You are an LLM judge evaluating whether an external tool's execution result
is satisfactory for answering the user's query.

User Query: {query}
Latest Tool Result: {tool_result}

Evaluation Criteria:
1. 'success': The tool result confirms successful execution or data retrieval
   (including mock tool results confirming execution). Valid results are 'success'.
2. 'needs_retry': The tool result explicitly indicates an error, failure, or exception.

Decide whether this execution is a 'success' or 'needs_retry'.
"""


def evaluate_node(state: AgentState) -> dict:
    """Đánh giá kết quả của công cụ — chốt chặn điều khiển retry-loop.

    Kiểm tra xem kết quả tool mới nhất đã đạt yêu cầu hay cần thử lại.
    """
    tool_results = state.get("tool_results", [])
    if not tool_results:
        return {
            "evaluation_result": "needs_retry",
            "events": [
                make_event(
                    "evaluate",
                    "completed",
                    "evaluated as needs_retry (no tool results)",
                    evaluation_result="needs_retry",
                    judge="empty_results_guard",
                )
            ],
        }

    latest_result = str(tool_results[-1])

    # Chốt chặn lỗi cố định (Deterministic Error Guard)
    if "ERROR" in latest_result:
        return {
            "evaluation_result": "needs_retry",
            "events": [
                make_event(
                    "evaluate",
                    "completed",
                    "evaluated as needs_retry (deterministic error guard)",
                    evaluation_result="needs_retry",
                    judge="deterministic_error_guard",
                )
            ],
        }

    # Đánh giá bằng LLM-as-judge
    query = state.get("query", "")
    llm = get_llm()
    structured_llm = llm.with_structured_output(EvaluationResult)
    prompt = EVALUATE_PROMPT.format(query=query, tool_result=latest_result)
    result = structured_llm.invoke(prompt)

    eval_decision = getattr(result, "evaluation_result", "success") if result else "success"

    return {
        "evaluation_result": eval_decision,
        "events": [
            make_event(
                "evaluate",
                "completed",
                f"evaluated as {eval_decision} (llm judge)",
                evaluation_result=eval_decision,
                judge="llm",
            )
        ],
    }


ANSWER_PROMPT = """You are an intelligent, grounded AI assistant answering the user's query.

User Query: {query}

Available Context:
{context_block}

Instructions:
1. Ground your response strictly in the provided query and context.
2. If tool results are provided, use the information directly to answer the user's question.
3. If approval information is provided (for risky operations), acknowledge the approval status.
4. If no tools/external context are needed (simple query), provide a direct and helpful answer.
5. If the context is insufficient, clearly state what is known and what information is missing.
"""


def answer_node(state: AgentState) -> dict:
    """Tạo câu trả lời cuối cùng sử dụng LLM thật, grounded theo ngữ cảnh có sẵn."""
    query = state.get("query", "")

    context_parts = []
    tool_results = state.get("tool_results", [])
    if tool_results:
        context_parts.append("Tool Results:\n" + "\n".join(f"- {r}" for r in tool_results))

    proposed_action = state.get("proposed_action")
    if proposed_action:
        context_parts.append(f"Proposed Action: {proposed_action}")

    approval = state.get("approval")
    if approval:
        if isinstance(approval, dict):
            status = "Approved" if approval.get("approved") else "Rejected"
            reviewer = approval.get("reviewer", "unknown")
            comment = approval.get("comment", "")
            context_parts.append(
                f"Approval Status: {status} (Reviewer: {reviewer}, Comment: {comment})"
            )
        else:
            context_parts.append(f"Approval Info: {approval}")

    if context_parts:
        context_block = "\n\n".join(context_parts)
    else:
        context_block = "No additional tool or approval context required."

    llm = get_llm()
    prompt = ANSWER_PROMPT.format(query=query, context_block=context_block)

    try:
        response = llm.invoke(prompt)
        if hasattr(response, "content"):
            content = response.content
            if isinstance(content, list):
                answer_text = "".join(
                    str(c.get("text", c)) if isinstance(c, dict) else str(c) for c in content
                )
            else:
                answer_text = str(content)
        elif isinstance(response, str):
            answer_text = response
        elif isinstance(response, BaseMessage):
            answer_text = str(response.content)
        else:
            answer_text = str(response)
    except Exception as exc:
        answer_text = f"Answering query '{query}' based on context: {context_block} (Error: {exc})"

    return {
        "final_answer": answer_text,
        "events": [make_event("answer", "completed", "grounded response generated")],
    }


def clarify_node(state: AgentState) -> dict:
    """Yêu cầu người dùng làm rõ khi truy vấn không đủ thông tin."""
    query = state.get("query", "")
    route = state.get("route", "")
    if route == "risky":
        question = (
            f"The proposed operation for '{query}' was not approved. "
            "Could you please provide an alternative request or confirm how you wish to proceed?"
        )
    else:
        question = (
            f"Could you please provide more specific details about your request: '{query}'?"
        )
    return {
        "pending_question": question,
        "final_answer": question,
        "events": [make_event("clarify", "completed", "clarification requested")],
    }


ask_clarification_node = clarify_node


def risky_action_node(state: AgentState) -> dict:
    """Chuẩn bị hành động rủi ro để chờ con người phê duyệt."""
    query = state.get("query", "")
    action_description = (
        f"Proposed action: Execute '{query}'. "
        "This action requires human approval because it may perform high-risk operations."
    )
    return {
        "proposed_action": action_description,
        "events": [make_event("risky_action", "completed", "risky action proposed")],
    }


def approval_node(state: AgentState) -> dict:
    """Bước phê duyệt có con người tham gia (Human-in-the-loop)."""
    return {
        "approval": {
            "approved": True,
            "reviewer": "mock-reviewer",
            "comment": "Approved for lab execution",
        },
        "events": [make_event("approval", "completed", "approval granted")],
    }


def retry_or_fallback_node(state: AgentState) -> dict:
    """Ghi nhận một lần thử lại (retry attempt)."""
    attempt = state.get("attempt", 0)
    new_attempt = attempt + 1
    error_msg = f"Attempt {attempt} encountered an issue; incrementing to attempt {new_attempt}"
    return {
        "attempt": new_attempt,
        "errors": [error_msg],
        "events": [make_event("retry", "completed", f"retry attempt {new_attempt}")],
    }


def dead_letter_node(state: AgentState) -> dict:
    """Xử lý các lỗi không thể khắc phục sau khi đã vượt quá số lần thử tối đa."""
    query = state.get("query", "")
    attempt = state.get("attempt", 0)
    ans = f"Request could not be completed after {attempt} attempts: {query}"
    event_msg = "max retries exceeded, escalated to dead letter"
    return {
        "final_answer": ans,
        "events": [make_event("dead_letter", "completed", event_msg)],
    }


def finalize_node(state: AgentState) -> dict:
    """Phát ra audit event kết thúc. Tất cả các route đều phải đi qua đây trước khi đến END."""
    return {
        "events": [make_event("finalize", "completed", "workflow finished")],
    }
