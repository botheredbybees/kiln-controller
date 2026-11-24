"""Firing supervisor: pure safety rules over a stream of kiln samples.

The oven feeds one Sample per control tick to Supervisor.evaluate, which returns a
Verdict. Units here are degrees C and seconds; duty is 0 to 1. This module imports
nothing from the oven, the GPIO layer or config, so it can be tested on its own.
SupervisorConfig.from_config is the one place the settings are read; it takes the
settings module as an argument.

A rule that says "for N seconds" starts a timer at the first sample over its limit and
trips when a later sample is still over the limit more than N seconds on. The ceiling
rule needs CONSECUTIVE samples in a row.

The plateau rule is different: when it first sees the kiln stalled it returns a "plateau"
verdict once, and stops the firing plateau_hold_s after that. The hold survives a lapse of
up to PLATEAU_LAPSE_S in the stall condition (a reading flickering across the margin, a dip
in mean duty). It clears only when the kiln gains plateau_gain over the window, when the
stall has been absent for longer than PLATEAU_LAPSE_S, or on reset(); a later stall then
reports "plateau" again. It stops at or after the hold (>=), where the other timer rules
trip strictly after their time; with plateau_hold_s = 0 it stops on the sample after
detection.
A sample whose time is earlier than the one before it (the clock stepped back) clears
every window and timer, and the sample then starts them afresh.

The heater-off rule trips at 720 s or more (the monitor script's behaviour); the other
timer rules trip strictly after their time.

Every rule is disabled by setting its main number to 0.
"""

from collections import deque
from dataclasses import dataclass

CONSECUTIVE = 2  # samples in a row for the ceiling rule
TARGET_WINDOW_S = 60.0  # how far back the target is compared to see it rise or fall
TARGET_MOVE_C = 0.1  # the target counts as rising or falling when it moved more than this
HEAT_WINDOW_S = 60.0  # the rise rate is measured over this long a stretch of heating
RATE_MIN_SAMPLES = 6  # and needs at least this many samples ...
RATE_MIN_SPAN_S = 30.0  # ... spanning at least this long
STUCK_LOOKBACK_S = 390.0  # sample history kept for the heater-off rule (it looks 360 s back)
STUCK_RISE_WINDOW_S = 360.0  # the heater-off rule compares with a sample at least this old
PLATEAU_LOOKBACK_EXTRA_S = 30.0  # the plateau window keeps this much more than its length
PLATEAU_WINDOW_FRACTION = 0.95  # the window counts as full once it spans this much of its length
PLATEAU_LAPSE_S = 120.0  # a plateau is forgotten once the stall has been absent this long
GLITCH_JUMP_C = 100.0  # a reading this far from the last good one is a glitch ...
GLITCH_MAX_SAMPLES = 3  # ... for at most this many samples in a row, then it is believed


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

    @classmethod
    def from_config(cls, cfg):
        """Build a config from the settings module, or None when supervision is off.

        Settings that are temperature deltas are in the display scale and are converted to C
        here; minutes become seconds. A setting an older config.py does not have keeps the
        script value, so this reads every setting with getattr and never raises for a missing one.
        """
        if not getattr(cfg, "supervisor_enabled", False):
            return None
        from temp import delta_to_c  # lazy: lib.temp reads config, this module stays pure

        d = cls()

        def read(name, default, convert=lambda v: v):
            value = getattr(cfg, "supervisor_" + name, None)
            return default if value is None else convert(value)

        def delta(name, default):
            return read(name, default, delta_to_c)

        def minutes(name, default):
            return read(name, default, lambda v: v * 60)

        return cls(
            peak_margin=delta("peak_margin", d.peak_margin),
            over_target_heater_on=delta("over_target_heater_on", d.over_target_heater_on),
            over_target_heater_on_s=read(
                "over_target_heater_on_seconds", d.over_target_heater_on_s
            ),
            over_target=delta("over_target", d.over_target),
            over_target_s=read("over_target_seconds", d.over_target_s),
            below_target=delta("below_target", d.below_target),
            below_target_s=minutes("below_target_minutes", d.below_target_s),
            max_rise_per_minute=delta("max_rise_per_minute", d.max_rise_per_minute),
            stuck_s=minutes("stuck_minutes", d.stuck_s),
            stuck_rise=delta("stuck_rise", d.stuck_rise),
            no_rise_s=minutes("no_rise_minutes", d.no_rise_s),
            no_rise_gain=delta("no_rise_gain", d.no_rise_gain),
            plateau_window_s=minutes("plateau_minutes", d.plateau_window_s),
            plateau_gain=delta("plateau_gain", d.plateau_gain),
            plateau_hold_s=minutes("plateau_hold_minutes", d.plateau_hold_s),
            catch_up_enabled=getattr(cfg, "kiln_must_catch_up", True),
        )


class Supervisor:
    def __init__(self, cfg: SupervisorConfig):
        self.cfg = cfg
        self.last_state = None
        self.reset()

    def reset(self) -> None:
        """Clear every window and timer."""
        self.peak_count = 0
        self.since = {}  # rule -> time its condition started holding
        self.target_hist = []  # (time, target), last TARGET_WINDOW_S
        self.heat_hist = deque()  # (time, temp) while the heater is on, last HEAT_WINDOW_S
        self.sample_hist = deque()  # (time, temp), last STUCK_LOOKBACK_S
        self.plateau_hist = deque()  # (time, temp, duty), last plateau_window_s plus a little
        self.plateau_at = None  # when the current plateau was reported
        self.plateau_lapse_at = None  # when the stall condition last stopped holding
        self.last_now = None
        self.last_temp = None  # the last reading that was not a glitch
        self.glitch_count = 0
        self.glitch = False  # the current sample is a glitch
        self.start_temp = None  # temperature of the first good sample, for the no-rise rule
        self.heat_time = 0.0  # seconds of heater-on time while the target was rising

    def evaluate(self, s: Sample) -> Verdict:
        if self.last_now is not None and s.now < self.last_now:
            self.reset()  # the clock stepped back: no window or timer means anything now
        if s.state != self.last_state:
            self.reset()
            self.last_state = s.state
        self._record(s)
        for rule in (
            self._peak_ceiling,
            self._over_target_heater_on,
            self._over_target,
            self._below_target,
            self._rise_rate,
            self._no_rise,
            self._heater_off_rising,
            self._plateau,
        ):
            verdict = rule(s)
            if verdict is not None:
                return verdict
        return OK

    def _held(self, key: str, active: bool, now: float):
        """Seconds the condition has held without a break, or None while it does not hold."""
        if not active:
            self.since.pop(key, None)
            return None
        return now - self.since.setdefault(key, now)

    def _record(self, s: Sample) -> None:
        self._record_target(s)
        dt = 0.0 if self.last_now is None else s.now - self.last_now
        self.last_now = s.now
        self._classify(s)
        heating = s.duty > 0
        if heating and self._target_rising(s):
            self.heat_time += dt
        if not heating:
            self.heat_hist.clear()
        if self.glitch:
            return
        if heating:
            self.heat_hist.append((s.now, s.temp))
            self._prune(self.heat_hist, s.now, HEAT_WINDOW_S)
        self.sample_hist.append((s.now, s.temp))
        self._prune(self.sample_hist, s.now, STUCK_LOOKBACK_S)
        self.plateau_hist.append((s.now, s.temp, s.duty))
        self._prune(self.plateau_hist, s.now, self.cfg.plateau_window_s + PLATEAU_LOOKBACK_EXTRA_S)

    @staticmethod
    def _prune(hist, now: float, window_s: float) -> None:
        while hist and now - hist[0][0] > window_s:
            hist.popleft()

    def _classify(self, s: Sample) -> None:
        """Flag a reading that jumps far from the last good one (seen once on the real kiln:
        600 C read 2.85 C for three samples). Rates, gains and the below-target rule ignore
        a glitch, but only a few in a row: a reading that stays put is believed."""
        jump = self.last_temp is not None and abs(s.temp - self.last_temp) > GLITCH_JUMP_C
        if jump and self.glitch_count < GLITCH_MAX_SAMPLES:
            self.glitch_count += 1
            self.glitch = True
            return
        self.glitch = False
        self.glitch_count = 0
        if jump:  # the kiln really moved: nothing measured before it compares with now
            self.heat_hist.clear()
            self.sample_hist.clear()
            self.plateau_hist.clear()
            self._clear_plateau()
            self.start_temp = None
            self.heat_time = 0.0
        self.last_temp = s.temp
        if self.start_temp is None:
            self.start_temp = s.temp

    def _record_target(self, s: Sample) -> None:
        self.target_hist.append((s.now, s.target))
        self.target_hist = [(t, x) for t, x in self.target_hist if s.now - t <= TARGET_WINDOW_S]

    def _target_rising(self, s: Sample) -> bool:
        return len(self.target_hist) > 1 and s.target - self.target_hist[0][1] > TARGET_MOVE_C

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
        held = self._held("over_target_heater_on", over, s.now)
        if held is not None and held > self.cfg.over_target_heater_on_s:
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
        held = self._held("over_target", over, s.now)
        if held is not None and held > self.cfg.over_target_s:
            return Verdict(
                "stop",
                "over_target",
                "%.1f C is more than %.0f C above the %.1f C target for %.0f s"
                % (s.temp, limit, s.target, held),
            )
        return None

    def _below_target(self, s: Sample):
        limit = self.cfg.below_target
        if self.glitch:  # a glitch neither starts nor clears the timer
            return None
        below = limit > 0 and s.state == "RUNNING" and s.temp < s.target - limit
        held = self._held("below_target", below, s.now)
        if held is not None and held > self.cfg.below_target_s:
            return Verdict(
                "stop",
                "below_target",
                "%.1f C is more than %.0f C below the %.1f C target for %.0f s: "
                "not following the profile" % (s.temp, limit, s.target, held),
            )
        return None

    def _rise_rate(self, s: Sample):
        limit = self.cfg.max_rise_per_minute
        hist = self.heat_hist
        if limit <= 0 or len(hist) < RATE_MIN_SAMPLES or self.glitch:
            return None
        span = hist[-1][0] - hist[0][0]
        if span < RATE_MIN_SPAN_S:
            return None
        rate = (hist[-1][1] - hist[0][1]) / (span / 60)
        if rate > limit:
            return Verdict(
                "stop",
                "rise_rate",
                "rising too fast while heating: %.1f C/min (limit %.0f C/min)" % (rate, limit),
            )
        return None

    def _no_rise(self, s: Sample):
        if self.cfg.no_rise_s <= 0 or self.glitch or self.start_temp is None:
            return None
        gain = s.temp - self.start_temp
        if self.heat_time > self.cfg.no_rise_s and gain < self.cfg.no_rise_gain:
            return Verdict(
                "stop",
                "no_rise",
                "no temperature rise after %.0f s of heating (%.2f C)" % (self.heat_time, gain),
            )
        return None

    def _heater_off_rising(self, s: Sample):
        limit = self.cfg.stuck_s
        held = self._held("heater_off", limit > 0 and s.duty <= 0.001, s.now)
        if held is None or held < limit or self.glitch:
            return None
        past = None  # the newest sample at least STUCK_RISE_WINDOW_S old
        for t, temp in self.sample_hist:
            if s.now - t < STUCK_RISE_WINDOW_S:
                break
            past = temp
        if past is None or s.temp - past < self.cfg.stuck_rise:
            return None
        return Verdict(
            "stop",
            "heater_off_rising",
            "heater may be stuck on: output 0 for %.0f min yet the temperature rose %.1f C in "
            "the last %.0f min (now %.1f C); cut the power at the wall"
            % (held / 60, s.temp - past, STUCK_RISE_WINDOW_S / 60, s.temp),
        )

    def _plateau(self, s: Sample):
        """Long stretch at near full power with no gain while still below the target. The first
        sample that sees it returns a "plateau" verdict; it stops only if it lasts the hold."""
        window = self.cfg.plateau_window_s
        hist = self.plateau_hist
        if window <= 0 or self.glitch:  # a glitch neither starts nor clears a plateau
            return None
        mean_duty = sum(duty for _, _, duty in hist) / len(hist)
        gain = s.temp - hist[0][1]
        stalled = (
            s.state == "RUNNING"
            and s.temp < s.target - self.cfg.plateau_min_below_target
            and s.now - hist[0][0] >= window * PLATEAU_WINDOW_FRACTION
            and mean_duty >= self.cfg.plateau_min_duty
            and gain < self.cfg.plateau_gain
        )
        if self.plateau_at is not None and gain >= self.cfg.plateau_gain:
            self._clear_plateau()  # the kiln really moved
        if self.plateau_at is None:
            if not stalled:
                return None
            self.plateau_at = s.now
            return Verdict(
                "plateau",
                "plateau",
                "the kiln may not reach the target: %.1f C vs %.1f C after %.0f min at %.0f%% "
                "output with a gain of %.1f C"
                % (s.temp, s.target, (s.now - hist[0][0]) / 60, mean_duty * 100, gain),
            )
        if stalled:
            self.plateau_lapse_at = None
        else:
            self.plateau_lapse_at = s.now if self.plateau_lapse_at is None else self.plateau_lapse_at
            if s.now - self.plateau_lapse_at > PLATEAU_LAPSE_S:
                self._clear_plateau()
                return None
        held = s.now - self.plateau_at
        if held >= self.cfg.plateau_hold_s:
            return Verdict(
                "stop",
                "plateau",
                "the kiln cannot reach the target: %.1f C vs %.1f C, still stalled %.0f min after "
                "it was reported (%.0f%% output, gain %.1f C)"
                % (s.temp, s.target, held / 60, mean_duty * 100, gain),
            )
        return None

    def _clear_plateau(self) -> None:
        self.plateau_at = None
        self.plateau_lapse_at = None
