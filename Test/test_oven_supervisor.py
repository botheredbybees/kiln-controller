"""Tests for the oven's use of the firing supervisor (Oven.supervisor_check).

Ovens are built bare (no control thread) with a fake board and a recording alert
sink, as in test_alert_detection.py. The clock is faked so a long firing runs fast.
Temperatures here are degrees C, the oven's internal unit."""

import json
import logging

import pytest

import config
import lib.oven as oven_module
from lib.alerts import ALERTS, AlertManager, AlertStore
from lib.oven import Oven, Profile
from lib.supervisor import OK, Verdict

PEAK_C = 1000.0
TARGET_C = 500.0


class FakeThermocoupleStatus:
    def over_error_limit(self):
        return False

    def duty_cycle_errors(self):
        return 0


class FakeTempSensor:
    def __init__(self, temp):
        self.temp = temp
        self.status = FakeThermocoupleStatus()

    def temperature(self):
        return self.temp


class FakeBoard:
    def __init__(self, temp):
        self.temp_sensor = FakeTempSensor(temp)


class RecordingSink:
    def __init__(self):
        self.calls = []

    def deliver(self, alert_id, label, criticality, context):
        self.calls.append((alert_id, label, criticality, context))

    def ids(self):
        return [c[0] for c in self.calls]


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture(autouse=True)
def quiet_oven(monkeypatch):
    """supervisor on, no restart-state writes, one clock for the oven and the alerts"""
    monkeypatch.setattr(config, "automatic_restarts", False)
    monkeypatch.setattr(config, "supervisor_enabled", True)
    clock = FakeClock()
    monkeypatch.setattr(oven_module.time, "time", clock)
    monkeypatch.setattr(oven_module.time, "monotonic", clock)  # the supervisor's clock
    return clock


@pytest.fixture
def clock(quiet_oven):
    return quiet_oven


def profile_c():
    data = [[0, 20], [3600, TARGET_C], [7200, PEAK_C], [9000, 300]]
    return Profile(json.dumps({"name": "test-supervisor", "data": data}))


def make_oven(temp=TARGET_C, state="RUNNING", target=TARGET_C, heat=0.0):
    store = AlertStore(state_file="/nonexistent/alerts.json")
    for a in ALERTS:
        store.enabled[a["id"]] = True
    manager = AlertManager(store)
    sink = RecordingSink()
    manager.add_sink(sink)
    oven = Oven()
    oven.board = FakeBoard(temp)
    oven.set_alert_manager(manager)
    oven.profile = profile_c()
    oven.state = state
    oven.target = target
    oven.heat = heat
    return oven, sink


def oven_heat_full():
    return float(config.sensor_time_wait)


def tick(oven, clock, seconds=2.0):
    clock.advance(seconds)
    oven.supervisor_check()


def test_supervisor_disabled_by_default_never_aborts(monkeypatch, clock):
    monkeypatch.setattr(config, "supervisor_enabled", False)
    oven, sink = make_oven(temp=TARGET_C + 12, heat=oven_heat_full())
    for _ in range(150):
        tick(oven, clock)
    assert oven.state == "RUNNING"
    assert sink.calls == []
    assert oven._supervisor is None


def test_supervisor_stop_aborts_the_run_and_alerts(clock):
    oven, sink = make_oven(temp=TARGET_C + 12, heat=oven_heat_full())
    for _ in range(15):  # 30 s of faked time
        tick(oven, clock)
    assert oven.state == "IDLE"
    ids = sink.ids()
    assert "supervisor_stop" in ids
    assert "run_aborted" in ids
    stop = sink.calls[ids.index("supervisor_stop")][3]
    assert stop["rule"] == "over_target_heater_on"
    assert stop["reason"]
    assert stop["profile"] == "test-supervisor"
    aborted = sink.calls[ids.index("run_aborted")][3]
    assert aborted["reason"] == "supervisor: over_target_heater_on"


def test_supervisor_plateau_alerts_once_and_keeps_running(monkeypatch, clock):
    oven, sink = make_oven(temp=TARGET_C - 20)
    verdicts = [Verdict("plateau", "plateau", "stalled")]
    monkeypatch.setattr(
        oven_module.Supervisor,
        "evaluate",
        lambda self, s: verdicts.pop(0) if verdicts else OK,
    )
    for _ in range(5):
        tick(oven, clock)
    assert sink.ids() == ["supervisor_plateau"]
    context = sink.calls[0][3]
    assert context["minutes"] > 0
    assert "temperature" in context and "target" in context
    assert oven.state == "RUNNING"


def test_supervisor_error_fails_open(monkeypatch, caplog, clock):
    oven, sink = make_oven(temp=TARGET_C)

    def boom(self, s):
        raise RuntimeError("bug in the supervisor")

    monkeypatch.setattr(oven_module.Supervisor, "evaluate", boom)
    with caplog.at_level(logging.ERROR, logger="lib.oven"):
        for _ in range(5):
            tick(oven, clock)
    assert oven.state == "RUNNING"
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    # the emergency check does not depend on the supervisor
    oven.board.temp_sensor.temp = (config.emergency_shutoff_temp - 32) * 5 / 9 + 10
    oven.reset_if_emergency()
    assert oven.state == "IDLE"


def test_supervisor_bad_setting_fails_open_once(monkeypatch, caplog, clock):
    monkeypatch.setattr(config, "supervisor_peak_margin", "54")  # a quoted string
    oven, sink = make_oven(temp=TARGET_C)
    with caplog.at_level(logging.ERROR, logger="lib.oven"):
        for _ in range(5):
            tick(oven, clock)
    assert oven.state == "RUNNING"
    assert len([r for r in caplog.records if r.levelno >= logging.ERROR]) == 1


@pytest.mark.parametrize(
    "setting, value",
    [
        ("supervisor_over_target_seconds", "120"),
        ("supervisor_below_target_minutes", "10"),
        ("supervisor_plateau_hold_minutes", "30"),
        ("supervisor_over_target", True),
    ],
)
def test_supervisor_bad_number_is_skipped_at_build_time_not_late(
    monkeypatch, caplog, clock, setting, value
):
    # a bad setting must fail when the supervisor is built, once, never inside evaluate
    # when a rule's condition finally holds (which would skip every later rule)
    monkeypatch.setattr(config, setting, value)
    oven, sink = make_oven(temp=TARGET_C + 60, heat=oven_heat_full())
    with caplog.at_level(logging.ERROR, logger="lib.oven"):
        for _ in range(200):
            tick(oven, clock)
    assert oven.state == "RUNNING"
    assert oven._supervisor is None
    assert sink.calls == []
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert setting in errors[0].getMessage()


def test_supervisor_enabled_as_a_string_leaves_it_off(monkeypatch, caplog, clock):
    monkeypatch.setattr(config, "supervisor_enabled", "False")  # truthy, but not True
    oven, sink = make_oven(temp=TARGET_C + 60, heat=oven_heat_full())
    with caplog.at_level(logging.ERROR, logger="lib.oven"):
        for _ in range(200):
            tick(oven, clock)
    assert oven.state == "RUNNING"
    assert oven._supervisor is None
    assert sink.calls == []


def test_supervisor_failure_never_raises_into_the_control_loop(monkeypatch, clock):
    oven, _ = make_oven()
    monkeypatch.setattr(oven, "_current_temp", lambda: 1 / 0)
    oven.supervisor_check()  # no exception
    assert oven.state == "RUNNING"


def test_supervisor_warns_once_when_catch_up_is_off(monkeypatch, caplog, clock):
    monkeypatch.setattr(config, "kiln_must_catch_up", False)
    oven, _ = make_oven()
    with caplog.at_level(logging.WARNING, logger="lib.oven"):
        tick(oven, clock)
        tick(oven, clock)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "kiln_must_catch_up" in warnings[0].getMessage()


def test_supervisor_builds_one_per_run_and_resets_with_the_run(clock):
    oven, _ = make_oven()
    tick(oven, clock)
    first = oven._supervisor
    assert first is not None
    tick(oven, clock)
    assert oven._supervisor is first  # one per run
    first.since["marker"] = 1.0  # state a run would leave in its windows
    oven.run_profile(profile_c(), allow_seek=False)
    assert oven._supervisor is None  # a new run starts without one
    tick(oven, clock)
    assert oven._supervisor is not first
    assert "marker" not in oven._supervisor.since
    oven.abort_run(reason="stopped")
    assert oven._supervisor is None


def test_supervisor_failed_flag_is_per_run(monkeypatch, caplog, clock):
    oven, _ = make_oven()
    monkeypatch.setattr(oven_module.Supervisor, "evaluate", lambda self, s: 1 / 0)
    with caplog.at_level(logging.ERROR, logger="lib.oven"):
        tick(oven, clock)
        oven.run_profile(profile_c(), allow_seek=False)
        tick(oven, clock)
    assert len([r for r in caplog.records if r.levelno >= logging.ERROR]) == 2


def test_supervisor_sample_uses_profile_peak_and_duty(monkeypatch, clock):
    oven, _ = make_oven(temp=480.0, target=490.0, heat=1.0)
    seen = []

    def spy(self, s):
        seen.append(s)
        return OK

    monkeypatch.setattr(oven_module.Supervisor, "evaluate", spy)
    tick(oven, clock)
    oven.heat = 5.0  # more than a cycle: clamped
    tick(oven, clock)
    oven.heat = -1.0
    tick(oven, clock)
    sample = seen[0]
    assert sample.peak == PEAK_C
    assert sample.temp == 480.0
    assert sample.target == 490.0
    assert sample.state == "RUNNING"
    assert sample.now == clock.now - 4.0
    assert sample.catching_up is False
    assert sample.duty == pytest.approx(1.0 / oven.time_step)
    assert seen[1].duty == 1.0
    assert seen[2].duty == 0.0


def test_supervisor_sample_carries_catching_up(monkeypatch, clock):
    oven, _ = make_oven()
    seen = []
    monkeypatch.setattr(oven_module.Supervisor, "evaluate", lambda self, s: seen.append(s) or OK)
    oven.catching_up = True
    tick(oven, clock)
    oven.catching_up = False
    tick(oven, clock)
    assert [s.catching_up for s in seen] == [True, False]


def test_supervisor_time_is_monotonic_not_wall_clock(monkeypatch, clock):
    # a step of the wall clock must not reach the supervisor's timers
    oven, _ = make_oven()
    seen = []
    monkeypatch.setattr(oven_module.Supervisor, "evaluate", lambda self, s: seen.append(s) or OK)
    wall = FakeClock(start=5_000_000.0)
    monkeypatch.setattr(oven_module.time, "time", wall)
    tick(oven, clock)
    wall.advance(-3600)  # the wall clock steps back an hour
    tick(oven, clock)
    assert [s.now for s in seen] == [clock.now - 2.0, clock.now]


def test_supervisor_skips_a_tick_when_the_temperature_is_unreadable(monkeypatch, clock):
    oven, _ = make_oven()
    seen = []
    monkeypatch.setattr(oven_module.Supervisor, "evaluate", lambda self, s: seen.append(s))
    monkeypatch.setattr(oven, "_current_temp", lambda: None)
    tick(oven, clock)
    assert seen == []
    assert oven.state == "RUNNING"


def drive(oven, monkeypatch, order=None):
    """make _run_once runnable on a bare oven. with an order list, the emergency check and
    the supervisor check only record that they ran; without one, both run for real"""
    for name in (
        "update_cost",
        "save_automatic_restart_state",
        "kiln_must_catch_up",
        "update_runtime",
        "update_target_temp",
        "heat_then_cool",
        "reset_if_schedule_ended",
        "check_safety_detectors",
        "check_catch_up_stalled",
    ):
        monkeypatch.setattr(oven, name, lambda: None, raising=False)
    if order is not None:
        monkeypatch.setattr(oven, "reset_if_emergency", lambda: order.append("emergency"))
        monkeypatch.setattr(oven, "supervisor_check", lambda: order.append("supervisor"))


@pytest.mark.parametrize("state", ["RUNNING", "PAUSED"])
def test_run_once_checks_the_supervisor_after_the_emergency_check(monkeypatch, state):
    oven, _ = make_oven(state=state)
    order = []
    drive(oven, monkeypatch, order)
    oven._run_once()
    assert order == ["emergency", "supervisor"]


@pytest.mark.parametrize("state", ["RUNNING", "PAUSED"])
def test_supervisor_stop_through_the_control_loop(monkeypatch, clock, state):
    oven, sink = make_oven(state=state, temp=PEAK_C + 100, heat=oven_heat_full())
    drive(oven, monkeypatch)
    for _ in range(3):
        clock.advance(2)
        oven._run_once()
        if oven.state == "IDLE":
            break  # an idle tick would sleep for real
    assert oven.state == "IDLE"
    assert "supervisor_stop" in sink.ids()
