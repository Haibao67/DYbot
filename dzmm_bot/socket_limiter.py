"""Per-room Socket pacing shared by ordinary and split replies."""
import asyncio
import time


class SocketLimiter:
    def __init__(self, *, initial=0.2, minimum=0.2, maximum=60.0, private=5.0,
                 success_window=30, success_step=0.1, cooldown=60.0, backoff_factor=2.0,
                 clock=None, sleeper=None, wall_clock=None):
        self.initial = float(initial)
        self.minimum = float(minimum)
        self.maximum = float(maximum)
        self.private = float(private)
        self.success_window = int(success_window)
        self.success_step = float(success_step)
        self.cooldown = float(cooldown)
        self.backoff_factor = float(backoff_factor)
        self.clock = clock or time.monotonic
        self.sleeper = sleeper or asyncio.sleep
        self.wall_clock = wall_clock or time.time
        self._rooms = {}
        self._last_room = None
        self.rate_limit_events = 0
        self.adjustments = 0

    def _state(self, room):
        room = str(room or "__default__")
        self._last_room = room
        return self._rooms.setdefault(room, {
            "current_interval": self.initial, "next_attempt": 0.0,
            "cooldown_until": 0.0, "last_rate_limited_at": None,
            "success_streak": 0,
        })

    @property
    def current_interval(self):
        room = self._last_room or "__default__"
        return self._state(room)["current_interval"]

    @property
    def success_streak(self):
        room = self._last_room or "__default__"
        return self._state(room)["success_streak"]

    @property
    def cooldown_until(self):
        room = self._last_room or "__default__"
        return self._state(room)["cooldown_until"]

    @classmethod
    def from_settings(cls, cfg, **clock_overrides):
        return cls(initial=getattr(cfg, "socket_initial_interval", 0.2),
                   minimum=getattr(cfg, "socket_min_interval", 0.2),
                   maximum=getattr(cfg, "socket_max_interval", 60.0),
                   private=getattr(cfg, "socket_private_interval", 5.0),
                   success_window=getattr(cfg, "socket_success_window", 30),
                   success_step=getattr(cfg, "socket_success_step", 0.1),
                   cooldown=getattr(cfg, "socket_rate_cooldown", 60.0),
                   backoff_factor=getattr(cfg, "socket_backoff_factor", 2.0),
                   **clock_overrides)

    async def wait(self, room, is_private):
        state = self._state(room)
        deadline = state["next_attempt"]
        delay = max(0.0, deadline - self.clock())
        if delay:
            await self.sleeper(delay)
        return delay

    def complete_attempt(self, room, is_private):
        """Pace the next attempt even when this ACK is ambiguous or the call raises."""
        state = self._state(room)
        now = self.clock()
        interval = max(state["current_interval"], self.private) if is_private else state["current_interval"]
        state["next_attempt"] = now + interval

    def accepted(self, room=None):
        state = self._state(room)
        if self.clock() < state["cooldown_until"]:
            return
        state["success_streak"] += 1
        if state["success_streak"] < self.success_window:
            return
        state["success_streak"] = 0
        reduced = max(self.minimum, round(state["current_interval"] - self.success_step, 3))
        if reduced < state["current_interval"]:
            state["current_interval"] = reduced
            self.adjustments += 1

    def rate_limited(self, room=None):
        state = self._state(room)
        now = self.clock()
        increased = min(self.maximum, max(state["current_interval"] * self.backoff_factor,
                                          state["current_interval"] + self.success_step))
        if increased > state["current_interval"]:
            state["current_interval"] = increased
            self.adjustments += 1
        state["success_streak"] = 0
        state["cooldown_until"] = now + self.cooldown
        state["next_attempt"] = max(state["next_attempt"], state["cooldown_until"])
        state["last_rate_limited_at"] = self.wall_clock()
        self.rate_limit_events += 1

    def snapshot(self, room=None):
        state = self._state(room if room is not None else (self._last_room or "__default__"))
        return {"current_interval_seconds": round(state["current_interval"], 3),
                "last_rate_limited_at": state["last_rate_limited_at"],
                "rate_limit_events": self.rate_limit_events,
                "adjustments": self.adjustments,
                "success_streak": state["success_streak"],
                "cooldown_remaining_seconds": round(max(0.0, state["cooldown_until"] - self.clock()), 3),
                "room_count": len(self._rooms)}
