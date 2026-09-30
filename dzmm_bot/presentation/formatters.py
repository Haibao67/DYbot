import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import unicodedata


def money(value):
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        amount = Decimal(0)
    if not amount.is_finite():
        amount = Decimal(0)
    return f"{amount:,.2f}".rstrip("0").rstrip(".")


def quantity(value):
    return f"{int(value):,}"


def duration(seconds):
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return "已完成/待收取"
    if not math.isfinite(seconds) or seconds <= 0:
        return "已完成/待收取"
    minutes = math.ceil(seconds / 60)
    hours, minutes = divmod(minutes, 60)
    return f"{seconds / 3600:.1f}h" if seconds >= 3600 else f"{minutes}分钟"


def long_duration(seconds):
    try:
        seconds = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        seconds = 0
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = math.ceil(remainder / 60)
    if minutes == 60:
        hours += 1
        minutes = 0
    if hours == 24:
        days += 1
        hours = 0
    return " ".join(f"{value}{label}" for value, label in ((days, "天"), (hours, "小时"), (minutes, "分钟")) if value) or "不足1分钟"


def bar(completed, total, width=6):
    ratio = completed / total if total > 0 else 0
    if not math.isfinite(ratio):
        ratio = 0
    filled = int(max(0, min(1, ratio)) * width)
    return "▓" * filled + "░" * (width - filled)


def name_escape(value):
    name = str(value or "玩家")
    name = "".join(" " if unicodedata.category(char).startswith("C") else char for char in name)
    return " ".join(name.replace("｜", "丨").replace("|", "丨").split())[:40] or "玩家"
