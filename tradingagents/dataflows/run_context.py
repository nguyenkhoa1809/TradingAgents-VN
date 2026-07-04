"""
run_context.py
==============
Scope ngày phân tích toàn cục cho 1 lần chạy pipeline (Task 4B — date fidelity).

Vấn đề: các news tool do LLM gọi (get_marketwire_news, get_news, ...) không nhìn
thấy LangGraph state, nên không biết trade_date. Khi backtest ngày quá khứ, tool
dùng datetime.now() → rò rỉ tin TƯƠNG LAI so với ngày phân tích (data leakage),
làm hỏng calibration data.

Giải pháp (Cách A): 1 ContextVar set ở đầu propagate(). Tool đọc từ đây; khi None
(production real-time) → fallback now() như cũ.

ContextVar an toàn với async/thread của LangGraph — mỗi ngữ cảnh chạy giữ giá trị
riêng, không cần truyền tham số xuyên suốt.
"""

from contextvars import ContextVar
from datetime import datetime
from typing import Optional

# None = production real-time (dùng now()). "YYYY-MM-DD" = backtest ngày cố định.
CURRENT_TRADE_DATE: ContextVar[Optional[str]] = ContextVar(
    "CURRENT_TRADE_DATE", default=None
)


def set_trade_date(trade_date: Optional[str]):
    """Set ngày phân tích cho ngữ cảnh hiện tại. Trả token để reset sau."""
    norm = str(trade_date)[:10] if trade_date else None
    return CURRENT_TRADE_DATE.set(norm)


def reset_trade_date(token) -> None:
    """Khôi phục giá trị trước đó (dùng token từ set_trade_date)."""
    try:
        CURRENT_TRADE_DATE.reset(token)
    except (ValueError, LookupError):
        pass


def get_trade_date() -> Optional[str]:
    """Ngày phân tích hiện tại ('YYYY-MM-DD') hoặc None nếu real-time."""
    return CURRENT_TRADE_DATE.get()


def effective_end_datetime() -> datetime:
    """Mốc thời gian trên (upper bound) cho mọi truy vấn tin.

    Backtest → cuối ngày trade_date; production → now(). Dùng cuối ngày (23:59:59)
    để bao trọn tin trong ngày trade_date.
    """
    td = get_trade_date()
    if td:
        try:
            d = datetime.strptime(td, "%Y-%m-%d")
            return d.replace(hour=23, minute=59, second=59)
        except ValueError:
            pass
    return datetime.now()
