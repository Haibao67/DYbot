"""Bounded in-process latency aggregates; never retain request or player data."""
import math
import threading


_BUCKETS_MS = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000)


class LatencyMetrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._series = {}

    def observe(self, name, elapsed_ms):
        if name not in {"core_lock_wait", "core_inbound", "core_invite", "core_inbound_total"}:
            return
        if not isinstance(elapsed_ms, (int, float)) or not math.isfinite(elapsed_ms) or elapsed_ms < 0:
            return
        with self._lock:
            series = self._series.setdefault(name, {"count": 0, "sum_ms": 0.0,
                "max_ms": 0.0, "buckets": [0] * (len(_BUCKETS_MS) + 1)})
            series["count"] += 1
            series["sum_ms"] += elapsed_ms
            series["max_ms"] = max(series["max_ms"], elapsed_ms)
            for index, boundary in enumerate(_BUCKETS_MS):
                if elapsed_ms <= boundary:
                    series["buckets"][index] += 1
                    break
            else:
                series["buckets"][-1] += 1

    def snapshot(self):
        with self._lock:
            result = {}
            for name, series in self._series.items():
                buckets = list(series["buckets"])
                item = {"samples": series["count"],
                        "mean_ms": round(series["sum_ms"] / series["count"], 1),
                        "max_ms": round(series["max_ms"], 1),
                        "bucket_upper_bounds_ms": list(_BUCKETS_MS) + [None],
                        "bucket_counts": buckets}
                for percentile in (50, 95):
                    target = math.ceil(series["count"] * percentile / 100)
                    cumulative = 0
                    value = None
                    for boundary, count in zip(item["bucket_upper_bounds_ms"], buckets):
                        cumulative += count
                        if cumulative >= target:
                            value = boundary
                            break
                    item[f"p{percentile}_upper_bound_ms"] = value
                result[name] = item
            return result
