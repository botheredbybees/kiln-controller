"""Firing supervisor: pure safety rules over a stream of kiln samples.

The oven feeds one Sample per control tick to Supervisor.evaluate, which returns a
Verdict. Units here are degrees C and seconds; duty is 0 to 1. This module imports
nothing from the oven, the GPIO layer or config, so it can be tested on its own.

A rule that says "for N seconds" starts a timer at the first sample over its limit and
trips when a later sample is still over the limit more than N seconds on. The ceiling
rule needs CONSECUTIVE samples in a row.

Every rule is disabled by setting its main number to 0.
"""

from dataclasses import dataclass

CONSECUTIVE = 2  # samples in a row for the ceiling rule
TARGET_WINDOW_S = 60.0  # how far back the target is compared to see it rise or fall
TARGET_MOVE_C = 0.1  # the target counts as rising or falling when it moved more than this


@dataclass(frozen=True)
class Sample:
    now: float  # seconds
    temp: float  # C
    target: float  # C
    duty: float  # 0 to 1
    state: str  # "RUNNING" or "PAUSED"
    peak: float  # C, highest point of the running profile


@dataclass(frozen=True)
class Verdict:
    action: str  # "ok", "plateau" or "stop"
    rule: str = ""
    reason: str = ""


OK = Verdict("ok")


@dataclass
class SupervisorConfig:
    """Rule numbers in C and seconds. The defaults are the values of the monitor script."""

    peak_margin: float = 30.0
    over_target_heater_on: float = 10.0
    over_target_heater_on_s: float = 20.0
    over_target: float = 25.0
    over_target_s: float = 120.0
    below_target: float = 40.0
    below_target_s: float = 600.0
    max_rise_per_minute: float = 30.0
    stuck_s: float = 720.0
    stuck_rise: float = 6.0
    no_rise_s: float = 600.0
    no_rise_gain: float = 3.0
    plateau_window_s: float = 1200.0
    plateau_gain: float = 5.0
    plateau_hold_s: float = 1800.0
    plateau_min_duty: float = 0.9
    plateau_min_below_target: float = 4.0
    catch_up_enabled: bool = True  # the controller's kiln_must_catch_up


class Supervisor:
    def __init__(self, cfg: SupervisorConfig):
        self.cfg = cfg
        self.last_state = None
        self.reset()

    def reset(self) -> None:
        """Clear every window and timer."""
        self.peak_count = 0
        self.over_heat_since = None
        self.over_any_since = None
        self.below_since = None
        self.target_hist = []  # (time, target), last TARGET_WINDOW_S

    def evaluate(self, s: Sample) -> Verdict:
        if s.state != self.last_state:
            self.reset()
            self.last_state = s.state
        self._record_target(s)
        for rule in (
            self._peak_ceiling,
            self._over_target_heater_on,
            self._over_target,
            self._below_target,
        ):
            verdict = rule(s)
            if verdict is not None:
                return verdict
        return OK

    def _record_target(self, s: Sample) -> None:
        self.target_hist.append((s.now, s.target))
        self.target_hist = [(t, x) for t, x in self.target_hist if s.now - t <= TARGET_WINDOW_S]

    def _target_falling(self, s: Sample) -> bool:
        return len(self.target_hist) > 1 and self.target_hist[0][1] - s.target > TARGET_MOVE_C

    def _skip_over_target(self, s: Sample) -> bool:
        # With kiln_must_catch_up off the controller lets the target fall while the kiln is
        # still hot, so being over the target then is expected.
        return not self.cfg.catch_up_enabled and self._target_falling(s)

    def _peak_ceiling(self, s: Sample):
        margin = self.cfg.peak_margin
        over = margin > 0 and s.temp > s.peak + margin
        self.peak_count = self.peak_count + 1 if over else 0
        if self.peak_count >= CONSECUTIVE:
            return Verdict(
                "stop",
                "peak_ceiling",
                "%.1f C is more than %.0f C above the profile's highest point (%.0f C)"
                % (s.temp, margin, s.peak),
            )
        return None

    def _over_target_heater_on(self, s: Sample):
        limit = self.cfg.over_target_heater_on
        over = (
            limit > 0
            and s.state == "RUNNING"
            and s.duty > 0
            and s.temp > s.target + limit
            and not self._skip_over_target(s)
        )
        if not over:
            self.over_heat_since = None
            return None
        if self.over_heat_since is None:
            self.over_heat_since = s.now
        held = s.now - self.over_heat_since
        if held > self.cfg.over_target_heater_on_s:
            return Verdict(
                "stop",
                "over_target_heater_on",
                "heater on at %.1f C, more than %.0f C above the %.1f C target, for %.0f s"
                % (s.temp, limit, s.target, held),
            )
        return None

    def _over_target(self, s: Sample):
        limit = self.cfg.over_target
        over = (
            limit > 0
            and s.state == "RUNNING"
            and s.temp > s.target + limit
            and not self._skip_over_target(s)
        )
        if not over:
            self.over_any_since = None
            return None
        if self.over_any_since is None:
            self.over_any_since = s.now
        held = s.now - self.over_any_since
        if held > self.cfg.over_target_s:
            return Verdict(
                "stop",
                "over_target",
                "%.1f C is more than %.0f C above the %.1f C target for %.0f s"
                % (s.temp, limit, s.target, held),
            )
        return None

    def _below_target(self, s: Sample):
        limit = self.cfg.below_target
        below = limit > 0 and s.state == "RUNNING" and s.temp < s.target - limit
        if not below:
            self.below_since = None
            return None
        if self.below_since is None:
            self.below_since = s.now
        held = s.now - self.below_since
        if held > self.cfg.below_target_s:
            return Verdict(
                "stop",
                "below_target",
                "%.1f C is more than %.0f C below the %.1f C target for %.0f s: "
                "not following the profile" % (s.temp, limit, s.target, held),
            )
        return None
