"""Tests for the firing supervisor: pure rules over a stream of samples."""

from lib.supervisor import OK, Sample, Supervisor, SupervisorConfig


def sample(now, temp, target, duty=0.0, state="RUNNING", peak=850.0):
    return Sample(now=now, temp=temp, target=target, duty=duty, state=state, peak=peak)


def run(sup, rows):
    return [sup.evaluate(s) for s in rows]


def make(**overrides):
    return Supervisor(SupervisorConfig(**overrides))


def test_peak_ceiling_needs_two_consecutive_samples():
    sup = make()
    assert sup.evaluate(sample(0, 881, 800)) == OK
    verdict = sup.evaluate(sample(2, 881, 800))
    assert verdict.action == "stop"
    assert verdict.rule == "peak_ceiling"
    assert "881.0 C" in verdict.reason

    # an ok sample in between restarts the count
    sup = make()
    verdicts = run(
        sup,
        [sample(0, 881, 800), sample(2, 800, 800), sample(4, 881, 800)],
    )
    assert verdicts == [OK, OK, OK]
    assert sup.evaluate(sample(6, 881, 800)).rule == "peak_ceiling"


def test_over_target_heater_on_trips_after_20_seconds():
    sup = make()
    verdicts = run(sup, [sample(t, 512, 500, duty=0.5) for t in range(0, 21)])
    assert all(v == OK for v in verdicts)
    verdict = sup.evaluate(sample(21, 512, 500, duty=0.5))
    assert verdict.action == "stop"
    assert verdict.rule == "over_target_heater_on"

    # heater off: this rule never trips (12 C over is under the 25 C any-heater limit)
    sup = make()
    verdicts = run(sup, [sample(t, 512, 500, duty=0.0) for t in range(0, 60)])
    assert all(v == OK for v in verdicts)

    # a sample back under 10 C resets the timer
    sup = make()
    run(sup, [sample(t, 512, 500, duty=0.5) for t in range(0, 15)])
    assert sup.evaluate(sample(15, 505, 500, duty=0.5)) == OK
    verdicts = run(sup, [sample(t, 512, 500, duty=0.5) for t in range(16, 37)])
    assert all(v == OK for v in verdicts)
    assert sup.evaluate(sample(37, 512, 500, duty=0.5)).rule == "over_target_heater_on"


def test_over_target_any_trips_after_120_seconds():
    sup = make()
    verdicts = run(sup, [sample(t, 526, 500) for t in range(0, 121)])
    assert all(v == OK for v in verdicts)
    verdict = sup.evaluate(sample(121, 526, 500))
    assert verdict.action == "stop"
    assert verdict.rule == "over_target"


def test_below_target_trips_after_600_seconds():
    sup = make()
    verdicts = run(sup, [sample(t, 459, 500) for t in range(0, 601)])
    assert all(v == OK for v in verdicts)
    verdict = sup.evaluate(sample(601, 459, 500))
    assert verdict.action == "stop"
    assert verdict.rule == "below_target"

    sup = make()
    verdicts = run(sup, [sample(t, 461, 500) for t in range(0, 1500, 3)])
    assert all(v == OK for v in verdicts)


def test_target_rules_ignore_paused_state():
    sup = make()
    rows = [sample(t, 512, 500, duty=0.5, state="PAUSED") for t in range(0, 200)]
    rows += [sample(t, 540, 500, state="PAUSED") for t in range(200, 500)]
    rows += [sample(t, 440, 500, state="PAUSED") for t in range(500, 1500)]
    assert all(v == OK for v in run(sup, rows))


def test_state_change_clears_windows():
    sup = make()
    verdicts = run(sup, [sample(t, 512, 500, duty=0.5) for t in range(0, 16)])
    assert all(v == OK for v in verdicts)
    assert sup.evaluate(sample(16, 512, 500, duty=0.5, state="PAUSED")) == OK
    # without the reset the timer (started at t=0) would trip at t=21
    verdicts = run(sup, [sample(t, 512, 500, duty=0.5) for t in range(17, 38)])
    assert all(v == OK for v in verdicts)
    verdict = sup.evaluate(sample(38, 512, 500, duty=0.5))
    assert verdict.rule == "over_target_heater_on"


def _falling_target_rows(n_s):
    # the target falls 0.5 C per 2 s; the kiln stays 30 C above it with the heater on
    return [
        sample(t, 700 - 0.25 * t + 30, 700 - 0.25 * t, duty=1.0)
        for t in range(0, n_s + 1, 2)
    ]


def test_over_target_rules_skip_a_falling_target_without_catch_up():
    sup = make(catch_up_enabled=False)
    assert all(v == OK for v in run(sup, _falling_target_rows(300)))

    sup = make(catch_up_enabled=True)
    rules = {v.rule for v in run(sup, _falling_target_rows(300)) if v.action == "stop"}
    assert "over_target_heater_on" in rules or "over_target" in rules


def test_ceiling_and_target_rules_are_off_when_zero():
    sup = make(peak_margin=0, over_target_heater_on=0, over_target=0, below_target=0)
    rows = [sample(t, 900, 500, duty=1.0) for t in range(0, 400, 2)]
    rows += [sample(t, 100, 500) for t in range(400, 1500, 2)]
    assert all(v == OK for v in run(sup, rows))
