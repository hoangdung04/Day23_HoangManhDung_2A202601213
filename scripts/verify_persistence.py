"""Kịch bản xác minh cơ chế lưu trữ và phục hồi trạng thái qua SQLite checkpointer (Persistence Verification)."""

from __future__ import annotations

import sys
from pathlib import Path
from dotenv import load_dotenv

# Thiet lap UTF-8 output cho console Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Nap bien moi truong tu .env
load_dotenv()

from langgraph_agent_lab.graph import build_graph
from langgraph_agent_lab.persistence import build_checkpointer
from langgraph_agent_lab.scenarios import load_scenarios
from langgraph_agent_lab.state import initial_state

DB_PATH = "outputs/verify_persistence_checkpoints.sqlite"
THREAD_ID = "persistence-verification-thread-01"


def run_write() -> None:
    """Process A: Ghi trạng thái và các checkpoint vào SQLite."""
    print("=== [Process A: WRITE] Khởi chạy graph và ghi checkpoint vào SQLite ===")
    db_file = Path(DB_PATH)
    if db_file.exists():
        db_file.unlink(missing_ok=True)
    for ext in ["-wal", "-shm"]:
        extra = Path(f"{DB_PATH}{ext}")
        if extra.exists():
            extra.unlink(missing_ok=True)

    checkpointer = build_checkpointer("sqlite", database_url=DB_PATH)
    graph = build_graph(checkpointer=checkpointer)

    scenarios = load_scenarios("data/sample/scenarios.jsonl")
    sample_scenario = scenarios[0]  # S01_simple

    state = initial_state(sample_scenario)
    state["thread_id"] = THREAD_ID

    config = {"configurable": {"thread_id": THREAD_ID}}
    print(f"Thực thi graph với thread_id='{THREAD_ID}'...")
    result = graph.invoke(state, config=config)

    print(f"Hoàn thành thực thi. Route='{result.get('route')}', Final Answer={bool(result.get('final_answer'))}")

    current_state = graph.get_state(config)
    history = list(graph.get_state_history(config))
    print(f"Process A - Current State Route: {current_state.values.get('route')}")
    print(f"Process A - Checkpoints đã lưu trong SQLite: {len(history)}")
    print("=== [Process A: WRITE] Kết thúc tiến trình ===")


def run_read() -> None:
    """Process B: Đọc lại trạng thái và checkpoint lịch sử từ cùng tệp SQLite trong tiến trình mới."""
    print("=== [Process B: READ] Đọc checkpoint từ SQLite sau khi Process A đã kết thúc ===")
    if not Path(DB_PATH).exists():
        print(f"Lỗi: Không tìm thấy tệp cơ sở dữ liệu '{DB_PATH}'")
        sys.exit(1)

    checkpointer = build_checkpointer("sqlite", database_url=DB_PATH)
    graph = build_graph(checkpointer=checkpointer)

    config = {"configurable": {"thread_id": THREAD_ID}}
    print(f"Truy vấn checkpoint với thread_id='{THREAD_ID}' từ cơ sở dữ liệu '{DB_PATH}'...")

    current_state = graph.get_state(config)
    if not current_state or not current_state.values:
        print(f"Lỗi: Không tìm thấy state cho thread_id='{THREAD_ID}'")
        sys.exit(1)

    print("-> Đã khôi phục thành công state từ SQLite!")
    print(f"   Route: {current_state.values.get('route')}")
    print(f"   Query: {current_state.values.get('query')}")
    print(f"   Final Answer: {current_state.values.get('final_answer')}")
    print(f"   Next step (kết thúc): {current_state.next}")

    history = list(graph.get_state_history(config))
    print(f"-> Tổng số checkpoint lịch sử (State History Snapshots): {len(history)}")
    for i, snapshot in enumerate(history):
        events_cnt = len(snapshot.values.get("events", []))
        node_name = snapshot.values.get("events", [{}])[-1].get("node", "start") if events_cnt > 0 else "init"
        print(f"   [{i+1}] Snapshot checkpoint_id={snapshot.config.get('configurable', {}).get('checkpoint_id')[:8]}... | Latest Node={node_name} | Next={snapshot.next}")

    print("=== [Process B: READ] Xác minh Persistence thành công 100%! ===")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode == "write":
        run_write()
    elif mode == "read":
        run_read()
    else:
        run_write()
        print()
        run_read()
