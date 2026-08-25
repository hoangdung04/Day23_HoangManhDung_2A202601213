"""Bộ điều hợp lưu trữ trạng thái và checkpoint (Checkpointer adapter)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def build_checkpointer(kind: str = "memory", database_url: str | None = None) -> Any | None:
    """Khởi tạo và trả về LangGraph checkpointer tương ứng với cấu hình.

    Hỗ trợ:
    - 'none': Không lưu checkpoint (None)
    - 'memory': Lưu tạm trong bộ nhớ RAM (MemorySaver)
    - 'sqlite': Lưu bền vững vào cơ sở dữ liệu SQLite (SqliteSaver) với chế độ WAL
    - 'postgres': Lưu trữ phân tán PostgreSQL (mở rộng trong tương lai)
    """
    if kind == "none":
        return None

    if kind == "memory":
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()

    if kind == "sqlite":
        from langgraph.checkpoint.sqlite import SqliteSaver

        # Chuẩn hóa đường dẫn tệp tin SQLite
        if database_url:
            cleaned_url = database_url
            if cleaned_url.startswith("sqlite:///"):
                cleaned_url = cleaned_url[len("sqlite:///"):]
            elif cleaned_url.startswith("sqlite://"):
                cleaned_url = cleaned_url[len("sqlite://"):]
            db_path = Path(cleaned_url)
        else:
            db_path = Path("outputs") / "langgraph_checkpoints.sqlite"

        db_path.parent.mkdir(parents=True, exist_ok=True)

        # Mở kết nối SQLite với hỗ trợ đa luồng và tối ưu ghi WAL
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")

        return SqliteSaver(conn=conn)

    if kind == "postgres":
        raise NotImplementedError(
            "TODO: Triển khai Postgres checkpointer (tùy chọn mở rộng cho môi trường phân tán)"
        )

    raise ValueError(f"Unknown checkpointer kind: {kind}")
