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


def _first_stop(verdicts):
    return next((i for i, v in enumerate(verdicts) if v.action == "stop"), None)


def test_rise_rate_trips_over_30_c_per_minute():
    sup = make()
    rows = [sample(t, 100 + 0.6 * t, 800, duty=1.0) for t in range(0, 62, 2)]  # 36 C/min
    verdicts = run(sup, rows)
    first = _first_stop(verdicts)
    assert first is not None
    assert verdicts[first].rule == "rise_rate"
    assert "36.0 C/min" in verdicts[first].reason

    sup = make()
    rows = [sample(t, 100 + 0.4 * t, 800, duty=1.0) for t in range(0, 62, 2)]  # 24 C/min
    assert all(v == OK for v in run(sup, rows))

    # a sample with duty 0 clears the window: neither half spans 30 s, the whole would
    sup = make()
    rows = [sample(t, 100 + 0.6 * t, 800, duty=1.0) for t in range(0, 26, 2)]
    rows.append(sample(26, 100 + 0.6 * 26, 800, duty=0.0))
    rows += [sample(t, 100 + 0.6 * t, 800, duty=1.0) for t in range(28, 52, 2)]
    assert all(v == OK for v in run(sup, rows))


def test_rise_rate_needs_six_samples_spanning_30_seconds():
    sup = make()
    rows = [sample(t, 100 + 10 * i, 800, duty=1.0) for i, t in enumerate((0, 2, 4))]
    assert all(v == OK for v in run(sup, rows))

    # six samples are not enough when they span under 30 s
    sup = make()
    rows = [sample(2 * i, 100 + 10 * i, 800, duty=1.0) for i in range(6)]
    assert all(v == OK for v in run(sup, rows))


def _slow_rise_rows(rise_c, duty_at=None, until=722):
    # temperature climbs rise_c over any 360 s; the target follows it so no target rule trips
    rows = []
    for t in range(0, until, 2):
        temp = 100 + rise_c / 360 * t
        duty = 0.5 if t == duty_at else 0.0
        rows.append(sample(t, temp, temp, duty=duty))
    return rows


def test_heater_off_rising_trips():
    sup = make()
    verdicts = run(sup, _slow_rise_rows(7))
    first = _first_stop(verdicts)
    assert first is not None
    assert verdicts[first].rule == "heater_off_rising"
    assert "cut the power at the wall" in verdicts[first].reason

    sup = make()
    assert all(v == OK for v in run(sup, _slow_rise_rows(5)))

    # a sample with the heater on restarts the 720 s timer
    sup = make()
    verdicts = run(sup, _slow_rise_rows(7, duty_at=600, until=1322))
    assert all(v == OK for v in verdicts)
    assert sup.evaluate(sample(1322, 100 + 7 / 360 * 1322, 100 + 7 / 360 * 1322)).rule == (
        "heater_off_rising"
    )


def test_coast_after_switch_off_does_not_trip():
    # the heater goes off at t=0; the kiln coasts up 8 C in 4 minutes, then cools
    sup = make()
    rows = []
    for t in range(0, 240 + 1800, 2):
        temp = 100 + 8 * t / 240 if t <= 240 else 108 - 0.05 * (t - 240)
        rows.append(sample(t, temp, temp, duty=0.0))
    assert all(v == OK for v in run(sup, rows))


def _no_rise_rows(temp_at, until, target_step=0.1):
    return [sample(t, temp_at(t), 300 + target_step * t / 2, duty=1.0) for t in range(0, until, 2)]


def test_no_rise_trips_after_600_seconds_of_heating():
    sup = make()
    verdicts = run(sup, _no_rise_rows(lambda t: 300.0, 622))
    first = _first_stop(verdicts)
    assert first is not None
    assert verdicts[first].rule == "no_rise"
    assert 600 < first * 2 <= 612

    # a gain of 3 C since the first sample is enough
    sup = make()
    assert all(v == OK for v in run(sup, _no_rise_rows(lambda t: 300.0 + (3.0 if t else 0), 700)))

    # heating time does not accumulate while the target is flat
    sup = make()
    assert all(v == OK for v in run(sup, _no_rise_rows(lambda t: 300.0, 1000, target_step=0.0)))


def test_short_low_glitch_does_not_stop():
    # 580 C at 5 C/min; for three samples the reading drops to 2.85 C, then recovers
    sup = make()
    rows = []
    for t in range(0, 800, 2):
        temp = 580 + 5 / 60 * t
        shown = 2.85 if 650 <= t <= 654 else temp
        rows.append(sample(t, shown, temp, duty=0.8))
    assert all(v == OK for v in run(sup, rows))


def test_sustained_low_reading_is_not_ignored():
    sup = make()
    rows = [sample(t, 600, 600, duty=0.0) for t in range(0, 100, 2)]
    rows += [sample(t, 2.85, 600, duty=0.0) for t in range(100, 800, 2)]
    verdicts = run(sup, rows)
    first = _first_stop(verdicts)
    assert first is not None
    assert verdicts[first].rule == "below_target"
    # three samples are ignored, the fourth starts the 600 s timer
    assert 700 < first * 2 <= 712


def test_heater_and_rate_rules_are_off_when_zero():
    sup = make(max_rise_per_minute=0, stuck_s=0, no_rise_s=0)
    rows = [sample(t, 100 + 0.6 * t, 800, duty=1.0) for t in range(0, 62, 2)]
    rows += _slow_rise_rows(7)
    rows += _no_rise_rows(lambda t: 300.0, 700)
    assert all(v == OK for v in run(sup, rows))
