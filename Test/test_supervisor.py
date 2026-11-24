"""Tests for the firing supervisor: pure rules over a stream of samples."""

import types

import pytest

import config
from lib.supervisor import OK, Sample, Supervisor, SupervisorConfig


def sample(now, temp, target, duty=0.0, state="RUNNING", peak=850.0, catching_up=False):
    return Sample(
        now=now,
        temp=temp,
        target=target,
        duty=duty,
        state=state,
        peak=peak,
        catching_up=catching_up,
    )


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
    return [sample(t, 700 - 0.25 * t + 30, 700 - 0.25 * t, duty=1.0) for t in range(0, n_s + 1, 2)]


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
    # three samples are ignored, the fourth (t=106) starts the 600 s timer, which trips
    # after 600 s: without the glitch filter the timer starts at t=100 and trips at 702
    assert first * 2 == 708


def test_believed_jump_after_long_heating_does_not_trip_no_rise():
    # 580 C ramping at 5 C/min with the target rising and the heater on: past 600 s of
    # heating time the no-rise rule has a gain to judge. The reading then drops to 2.85 C
    # and stays there, so after three ignored samples the 4th is believed. The gain is
    # measured again from that reading, so heating time must restart too: no `no_rise`
    # stop on that tick. below_target starts its own 600 s timer, far from tripping in
    # 20 s. The recovery is a second believed jump and must not look like a fast rise.
    sup = make()
    rows = []
    for t in range(0, 770, 2):
        ramp = 580 + 5 / 60 * t
        shown = 2.85 if 710 <= t <= 730 else ramp
        rows.append(sample(t, shown, ramp, duty=0.8))
    verdicts = run(sup, rows)
    assert all(v == OK for v in verdicts), [v for v in verdicts if v != OK][:1]


def test_heater_and_rate_rules_are_off_when_zero():
    sup = make(max_rise_per_minute=0, stuck_s=0, no_rise_s=0)
    rows = [sample(t, 100 + 0.6 * t, 800, duty=1.0) for t in range(0, 62, 2)]
    rows += _slow_rise_rows(7)
    rows += _no_rise_rows(lambda t: 300.0, 700)
    assert all(v == OK for v in run(sup, rows))


def _flat_rows(start, stop, temp=864.0, target=870.0, duty=1.0, step=2):
    return [sample(t, temp, target, duty=duty) for t in range(start, stop, step)]


def _first_index(verdicts, action):
    return next((i for i, v in enumerate(verdicts) if v.action == action), None)


def test_plateau_detected_after_20_minutes_at_full_power():
    sup = make()
    verdicts = run(sup, _flat_rows(0, 1700))
    plateaus = [i * 2 for i, v in enumerate(verdicts) if v.action == "plateau"]
    assert len(plateaus) == 1
    assert 1140 <= plateaus[0] <= 1200
    assert verdicts[_first_index(verdicts, "plateau")].rule == "plateau"
    assert all(v.action != "stop" for v in verdicts)


def test_plateau_stops_after_the_hold():
    sup = make()
    verdicts = run(sup, _flat_rows(0, 3200))
    detected = _first_index(verdicts, "plateau") * 2
    stop = _first_index(verdicts, "stop") * 2
    assert stop == detected + 1800
    assert verdicts[stop // 2].rule == "plateau"
    assert all(v.action == "ok" for v in verdicts[detected // 2 + 1 : stop // 2])


def test_plateau_clears_when_the_kiln_gains_5_c():
    # flat at 864 C, then a 6 C gain at t=1500, well before the 30 minute hold ends
    sup = make()
    rows = _flat_rows(0, 1500, target=880) + _flat_rows(1500, 3100, temp=870.0, target=880)
    verdicts = run(sup, rows)
    assert all(v.action != "stop" for v in verdicts)
    plateaus = [i * 2 for i, v in enumerate(verdicts) if v.action == "plateau"]
    assert len(plateaus) == 2  # reported again for the second plateau
    assert 1140 <= plateaus[0] <= 1200
    assert plateaus[1] > 1500 + 1140  # only once the gain has left the window


def test_not_a_plateau_when_close_to_target():
    sup = make()
    assert all(v == OK for v in run(sup, _flat_rows(0, 3600, temp=868.0)))


def test_not_a_plateau_at_low_duty():
    sup = make()
    assert all(v == OK for v in run(sup, _flat_rows(0, 3600, temp=850.0, duty=0.5)))


def test_first_sample_at_full_duty_is_harmless():
    # a flat stall at 89% output with a first sample at 100% (the PID has not settled): the
    # mean over the window stays under 0.9, so the first sample must not make a plateau
    sup = make()
    rows = [sample(0, 862.0, 870.0, duty=1.0)]
    rows += _flat_rows(2, 2400, temp=862.0, duty=0.89)
    verdicts = run(sup, rows)
    assert all(v == OK for v in verdicts), [v for v in verdicts if v != OK][:1]
    # the first sample is still in the window while the span is long enough to judge
    assert sup.plateau_hist[0][0] > 0
    assert sum(d for _, _, d in sup.plateau_hist) / len(sup.plateau_hist) < 0.9


def test_low_glitch_does_not_create_a_plateau():
    # a slow climb (10 C in 20 minutes at full power, 20 C under target); a three-sample
    # low reading must not look like a stall
    sup = make()
    rows = []
    for t in range(0, 2400, 2):
        temp = 700 + t / 120
        shown = 2.85 if 1500 <= t <= 1504 else temp
        rows.append(sample(t, shown, temp + 20, duty=1.0))
    assert all(v == OK for v in run(sup, rows))


def test_high_glitch_does_not_clear_a_plateau():
    sup = make()
    rows = _flat_rows(0, 1900)
    rows += [sample(t, 1170.0, 870.0, duty=1.0, peak=2000.0) for t in (1900, 1902, 1904)]
    rows += _flat_rows(1906, 3200)
    verdicts = run(sup, rows)
    assert [v.action for v in verdicts].count("plateau") == 1
    assert verdicts[_first_index(verdicts, "stop")].rule == "plateau"


def test_clock_stepping_backwards_resets_windows():
    sup = make()
    start = 10000
    assert all(
        v == OK for v in run(sup, [sample(start + t, 800, 805, duty=1.0) for t in range(600)])
    )
    # 15 s of 12 C over with the heater on; the timer is running
    assert all(
        v == OK for v in run(sup, [sample(start + 600 + t, 812, 800, duty=0.5) for t in range(15)])
    )
    back = start + 600 - 3600
    assert sup.evaluate(sample(back, 812, 800, duty=0.5)) == OK
    # the 20 s timer starts again from the sample after the step
    verdicts = run(sup, [sample(back + t, 812, 800, duty=0.5) for t in range(1, 21)])
    assert all(v == OK for v in verdicts)
    assert sup.evaluate(sample(back + 21, 812, 800, duty=0.5)).rule == "over_target_heater_on"


def test_plateau_is_off_when_the_window_is_zero():
    sup = make(plateau_window_s=0)
    assert all(v == OK for v in run(sup, _flat_rows(0, 3600)))


def _stop_times(verdicts, step=2):
    return [i * step for i, v in enumerate(verdicts) if v.action == "stop"]


def test_plateau_hold_survives_flicker_across_the_margin():
    # after detection the reading flickers across target - 4 C every sample
    sup = make()
    rows = _flat_rows(0, 1200)
    rows += [
        sample(t, 866.0 if t % 4 == 0 else 865.9, 870.0, duty=1.0) for t in range(1200, 3200, 2)
    ]
    verdicts = run(sup, rows)
    assert [v.action for v in verdicts].count("plateau") == 1
    # the hold ends at 2940, a sample inside the flicker's lapse; the stop waits for the next
    # stalled sample
    assert _stop_times(verdicts)[0] == 1140 + 1800 + 2
    assert verdicts[(1140 + 1800 + 2) // 2].rule == "plateau"


def test_plateau_hold_survives_a_short_dip_in_mean_duty():
    # mean duty sits just over 0.9; six samples at 0.80 push it well under for a while (about
    # 0.6 of duty-seconds below the 0.3 the margin allows), then six at full power bring it back
    sup = make()
    rows = _flat_rows(0, 1200, duty=0.9005)
    for t in range(1200, 3200, 2):
        duty = 0.80 if 1200 <= t < 1212 else (1.0 if t < 1224 else 0.9005)
        rows.append(sample(t, 864.0, 870.0, duty=duty))
    lapsed = False
    verdicts = []
    for row in rows:
        verdicts.append(sup.evaluate(row))
        lapsed = lapsed or sup.plateau_lapse_at is not None
    assert lapsed  # the dip really took the stall away for a moment
    assert [v.action for v in verdicts].count("plateau") == 1
    assert _stop_times(verdicts)[0] == 1140 + 1800


def test_plateau_hold_restarts_after_a_real_recovery():
    # 131 s within 3 C of the target (longer than the 120 s lapse), then stalled again
    sup = make()
    rows = _flat_rows(0, 1500)
    rows += _flat_rows(1500, 1632, temp=867.0)
    rows += _flat_rows(1632, 3600)
    verdicts = run(sup, rows)
    plateaus = [i * 2 for i, v in enumerate(verdicts) if v.action == "plateau"]
    assert plateaus == [1140, 1632]
    assert _stop_times(verdicts) == [1632 + 1800 + 2 * k for k in range(len(_stop_times(verdicts)))]
    assert _stop_times(verdicts)[0] == 1632 + 1800


def test_plateau_does_not_stop_on_a_lapse_sample_at_hold_expiry():
    # detected at 1140, the hold ends at 2940; the sample at 2940 reads 3 C under the target
    # (not stalled now), the next one is stalled again
    sup = make()
    rows = _flat_rows(0, 2940)
    rows.append(sample(2940, 867.0, 870.0, duty=1.0))
    rows += _flat_rows(2942, 3000)
    verdicts = run(sup, rows)
    assert _first_index(verdicts, "plateau") * 2 == 1140
    assert _stop_times(verdicts)[0] == 2942
    assert verdicts[2940 // 2] == OK


def test_plateau_is_found_under_a_small_pid_window_while_catching_up():
    # the schedule is frozen one window behind the kiln (2.8 C here), so a stalled kiln never
    # gets the 4 C below the target the plain rule asks for
    sup = make()
    rows = [sample(t, 867.2, 870.0, duty=1.0, catching_up=True) for t in range(0, 3200, 2)]
    verdicts = run(sup, rows)
    detected = _first_index(verdicts, "plateau") * 2
    assert 1140 <= detected <= 1200
    assert _stop_times(verdicts)[0] == detected + 1800
    assert verdicts[_stop_times(verdicts)[0] // 2].rule == "plateau"


def test_small_gap_without_catch_up_is_not_a_plateau():
    sup = make()
    rows = [sample(t, 867.2, 870.0, duty=1.0, catching_up=False) for t in range(0, 3200, 2)]
    assert all(v == OK for v in run(sup, rows))


def test_catching_up_does_not_make_a_kiln_at_the_target_a_plateau():
    # at or over the target there is nothing to catch up to
    sup = make()
    rows = [sample(t, 870.0, 870.0, duty=1.0, catching_up=True) for t in range(0, 3200, 2)]
    assert all(v == OK for v in run(sup, rows))


def _cooling_rows(start_c, per_min, target, until, duty=0.0):
    return [
        sample(t, start_c - per_min * t / 60, target, duty=duty, peak=1000.0)
        for t in range(0, until, 2)
    ]


def test_over_target_ignores_a_kiln_cooling_toward_a_frozen_target_with_the_heater_off():
    # a warm kiln at 200 C, heater off, cooling 1 C/min, the profile starts at 20 C
    sup = make()
    assert all(v == OK for v in run(sup, _cooling_rows(200.0, 1.0, 20.0, 1200)))


def test_over_target_still_stops_a_kiln_over_target_with_the_heater_off_and_not_falling():
    for per_min in (0.0, -1.0):  # flat, then rising (a stuck relay)
        sup = make()
        verdicts = run(sup, _cooling_rows(230.0, per_min, 200.0, 400))
        first = _first_stop(verdicts)
        assert first is not None, per_min
        assert verdicts[first].rule == "over_target"
        assert 120 < first * 2 <= 124


def test_over_target_heater_on_still_stops_a_cooling_kiln_with_the_heater_on():
    sup = make()
    verdicts = run(sup, _cooling_rows(230.0, 1.0, 200.0, 400, duty=0.5))
    first = _first_stop(verdicts)
    assert verdicts[first].rule == "over_target_heater_on"
    assert 20 < first * 2 <= 24


def test_over_target_timer_clears_while_the_kiln_is_exempt():
    # 30 C over and flat for 100 s (timer running), cooling for 120 s (exempt: timer cleared),
    # then flat again: the 120 s are counted from the new start, not from t=0
    sup = make()
    rows = [sample(t, 230.0, 200.0, peak=1000.0) for t in range(0, 100, 2)]
    rows += [sample(t, 230.0 - (t - 98) / 60 * 2, 200.0, peak=1000.0) for t in range(100, 220, 2)]
    flat = 230.0 - 122 / 60 * 2
    rows += [sample(t, flat, 200.0, peak=1000.0) for t in range(220, 500, 2)]
    verdicts = run(sup, rows)
    stop = _stop_times(verdicts)[0]
    assert verdicts[stop // 2].rule == "over_target"
    # not stopped before the new timer's 120 s; the fall must be 60 s old before it counts
    assert stop > 220 + 120 - 2


def test_a_glitch_does_not_clear_the_over_target_timers():
    # 30 C over and flat; a three-sample low reading in the middle must not restart the timer
    steady = [sample(t, 530.0, 500.0, duty=0.0, peak=1000.0) for t in range(0, 300, 2)]
    clean = _stop_times(run(make(), steady))[0]
    glitched = [
        sample(t, 2.85 if 60 <= t <= 64 else 530.0, 500.0, duty=0.0, peak=1000.0)
        for t in range(0, 300, 2)
    ]
    assert _stop_times(run(make(), glitched))[0] == clean

    steady = [sample(t, 515.0, 500.0, duty=0.5, peak=1000.0) for t in range(0, 100, 2)]
    clean = _stop_times(run(make(), steady))[0]
    glitched = [
        sample(t, 2.85 if 10 <= t <= 14 else 515.0, 500.0, duty=0.5, peak=1000.0)
        for t in range(0, 100, 2)
    ]
    assert _stop_times(run(make(), glitched))[0] == clean


def test_a_glitch_does_not_clear_the_peak_ceiling_count():
    sup = make()
    rows = [sample(0, 881, 800), sample(2, 2.85, 800), sample(4, 881, 800)]
    verdicts = run(sup, rows)
    assert verdicts[:2] == [OK, OK]
    assert verdicts[2].rule == "peak_ceiling"


# ---- SupervisorConfig.from_config ------------------------------------------------


def _cfg(**settings):
    return types.SimpleNamespace(**settings)


def test_from_config_returns_none_when_disabled_or_missing():
    assert SupervisorConfig.from_config(_cfg()) is None
    assert SupervisorConfig.from_config(_cfg(supervisor_enabled=False)) is None


@pytest.mark.parametrize("value", ["False", "True", "", 1, 0, None])
def test_from_config_needs_a_real_bool_to_turn_on(value):
    assert SupervisorConfig.from_config(_cfg(supervisor_enabled=value)) is None


@pytest.mark.parametrize(
    "setting",
    [
        "supervisor_over_target_seconds",
        "supervisor_over_target_heater_on_seconds",
        "supervisor_below_target_minutes",
        "supervisor_stuck_minutes",
        "supervisor_no_rise_minutes",
        "supervisor_plateau_minutes",
        "supervisor_plateau_hold_minutes",
        "supervisor_peak_margin",
        "supervisor_over_target",
        "supervisor_plateau_gain",
    ],
)
@pytest.mark.parametrize("bad", ["10", True, [10], float("nan")])
def test_from_config_rejects_a_bad_number_at_build_time(setting, bad):
    with pytest.raises((TypeError, ValueError)):
        SupervisorConfig.from_config(_cfg(supervisor_enabled=True, **{setting: bad}))


def test_from_config_uses_the_script_values_when_no_rule_is_set():
    # an older config.py that only turns the supervisor on
    got = SupervisorConfig.from_config(_cfg(supervisor_enabled=True))
    assert got == SupervisorConfig()


def test_from_config_converts_display_deltas_to_celsius(monkeypatch):
    monkeypatch.setattr(config, "temp_scale", "f")
    got = SupervisorConfig.from_config(
        _cfg(
            supervisor_enabled=True,
            supervisor_peak_margin=54,
            supervisor_below_target_minutes=10,
            supervisor_stuck_minutes=12,
            supervisor_plateau_hold_minutes=30,
            supervisor_over_target_seconds=120,
        )
    )
    assert got.peak_margin == pytest.approx(30.0)
    assert got.below_target_s == 600
    assert got.stuck_s == 720
    assert got.plateau_hold_s == 1800
    assert got.over_target_s == 120  # seconds are not scaled


def test_from_config_keeps_celsius_deltas_in_a_celsius_config(monkeypatch):
    monkeypatch.setattr(config, "temp_scale", "c")
    got = SupervisorConfig.from_config(_cfg(supervisor_enabled=True, supervisor_peak_margin=30))
    assert got.peak_margin == pytest.approx(30.0)


def test_from_config_reads_every_setting_into_its_field(monkeypatch):
    monkeypatch.setattr(config, "temp_scale", "c")
    got = SupervisorConfig.from_config(
        _cfg(
            supervisor_enabled=True,
            supervisor_peak_margin=1,
            supervisor_over_target_heater_on=2,
            supervisor_over_target_heater_on_seconds=3,
            supervisor_over_target=4,
            supervisor_over_target_seconds=5,
            supervisor_below_target=6,
            supervisor_below_target_minutes=7,
            supervisor_max_rise_per_minute=8,
            supervisor_stuck_minutes=9,
            supervisor_stuck_rise=10,
            supervisor_no_rise_minutes=11,
            supervisor_no_rise_gain=12,
            supervisor_plateau_minutes=13,
            supervisor_plateau_gain=14,
            supervisor_plateau_hold_minutes=15,
            kiln_must_catch_up=False,
        )
    )
    assert (got.peak_margin, got.over_target_heater_on, got.over_target_heater_on_s) == (1, 2, 3)
    assert (got.over_target, got.over_target_s, got.below_target) == (4, 5, 6)
    assert (got.below_target_s, got.max_rise_per_minute, got.stuck_s) == (420, 8, 540)
    assert (got.stuck_rise, got.no_rise_s, got.no_rise_gain) == (10, 660, 12)
    assert (got.plateau_window_s, got.plateau_gain, got.plateau_hold_s) == (780, 14, 900)
    assert got.catch_up_enabled is False


def test_from_config_follows_kiln_must_catch_up():
    assert SupervisorConfig.from_config(_cfg(supervisor_enabled=True)).catch_up_enabled is True
    on = _cfg(supervisor_enabled=True, kiln_must_catch_up=True)
    assert SupervisorConfig.from_config(on).catch_up_enabled is True


def test_from_config_zero_disables_a_rule(monkeypatch):
    monkeypatch.setattr(config, "temp_scale", "f")
    got = SupervisorConfig.from_config(_cfg(supervisor_enabled=True, supervisor_over_target=0))
    assert got.over_target == 0
    sup = Supervisor(got)
    rows = [sample(t, 576.0, 550.0) for t in range(0, 301, 2)]  # 26 C over, heater off
    assert all(v == OK for v in run(sup, rows))
    # the same input with the rule on does trip, so the zero is what silences it
    sup = Supervisor(SupervisorConfig.from_config(_cfg(supervisor_enabled=True)))
    assert any(v.rule == "over_target" for v in run(sup, rows))


# Cases ported from selftest() in tools/kiln-supervisor/profile_monitor.py. Each one rebuilds the
# script's constructed sequence as Samples (the script's `heat` tuple value is already a 0 to 1
# duty) and expects the same rule to trip, or the same silence. The script ran its plateau
# ("cannot reach the target") rule as a stop; here it first reports "plateau" and stops after the
# hold, so that case expects the plateau verdict and then the stop.
#
# Not ported, because the rule is not in lib/supervisor.py:
#   - absolute ceiling, twice in a row (script 1051 C): the oven's own emergency_shutoff_temp does it
#   - one reading over the absolute ceiling stays quiet
#   - run length past the profile length plus the allowance, and the same with a long allowance
#   - a normal cool-down after the run (state IDLE): the oven never calls the supervisor in IDLE
#   - the two status-feed reconnect cases (a laptop concern, there is no feed here)
def _script_ramp(n, c0=22.0, per_step=0.17, duty=1.0, peak=150.0):
    return [
        sample(i * 2, c0 + i * per_step, c0 + i * per_step, duty=duty, peak=peak) for i in range(n)
    ]


def _script_cases():
    cases = []

    def add(name, rows, expect):
        cases.append(pytest.param(rows, expect, id=name))

    add("healthy_ramp", _script_ramp(400), None)
    hold = [
        sample(i * 2, 150.0, 150.0, duty=1.0 if i % 3 == 0 else 0.0, peak=150.0) for i in range(300)
    ]
    add("healthy_hold", hold, None)
    swing = [
        sample(i * 2, 150.0 + (4 if i % 20 < 10 else -4), 150.0, peak=150.0) for i in range(300)
    ]
    add("small_swings_around_the_target", swing, None)
    over_heat = [sample(i * 2, 165.0, 150.0, duty=1.0, peak=150.0) for i in range(20)]
    add("heater_on_well_above_target", over_heat, ("stop", "over_target_heater_on"))
    over_any = [sample(i * 10, 178.0, 150.0, peak=200.0) for i in range(20)]
    add("sustained_overshoot", over_any, ("stop", "over_target"))
    lag = [sample(i * 30, 60.0, 150.0, duty=1.0, peak=150.0) for i in range(30)]
    add("sustained_lag", lag, ("stop", "below_target"))
    fast = [sample(i * 2, 22 + i * 1.2, 150.0, duty=1.0, peak=300.0) for i in range(60)]
    add("runaway_rate", fast, ("stop", "rise_rate"))
    # target rising, reading dead flat
    stuck = [sample(i * 2, 22.0, 22.0 + i * 0.05, duty=1.0, peak=150.0) for i in range(400)]
    add("no_rise_while_the_target_rises", stuck, ("stop", "no_rise"))
    # far below target: the lag rule catches it first
    stuck_far = [sample(i * 2, 22.0, 150.0, duty=1.0, peak=150.0) for i in range(400)]
    add("stuck_reading_far_below_target", stuck_far, ("stop", "below_target"))
    peak_over = [sample(0, 190.0, 150.0, peak=150.0), sample(2, 191.0, 150.0, peak=150.0)]
    add("above_the_peak_plus_margin_twice", peak_over, ("stop", "peak_ceiling"))
    one_high = [sample(0, 190.0, 150.0, peak=150.0), sample(2, 150.0, 150.0, peak=150.0)]
    add("one_bad_high_reading", one_high, None)
    # a controlled cool-down: target falling, heater cycling to slow it, reading falling 1 C/min
    cool_run = [
        sample(
            i * 10,
            900.0 - i * (10 / 60),
            900.0 - i * (10 / 60) + 1.0,
            duty=0.3 if i % 2 else 0.0,
            peak=1000.0,
        )
        for i in range(1080)
    ]
    add("controlled_cool_down_with_the_heater_cycling", cool_run, None)
    # at the top of the ramp: heater off for 4 minutes while the kiln coasts up 20 C, then falls
    coast = [
        sample(
            i * 2,
            1000.0 + (min(i, 60) * 0.33 if i <= 60 else 20.0 - (i - 60) * 0.1),
            1000.0,
            duty=0.0 if i > 5 else 0.5,
            peak=1000.0,
        )
        for i in range(240)
    ]
    add("short_coast_after_the_heater_goes_off", coast, None)
    # 25 min at full power, flat, 20 C below the target
    stall = [sample(i * 10, 700.0 + i * 0.001, 720.0, duty=1.0, peak=1000.0) for i in range(150)]
    add("full_power_with_no_gain_below_target", stall, ("plateau", "plateau"))
    # gaining 1.2 C/min at full power, 6 C behind (the controller holds the schedule near the kiln)
    slow = [
        sample(i * 10, 700.0 + i * 0.2, 706.0 + i * 0.2, duty=1.0, peak=1000.0) for i in range(150)
    ]
    add("slow_climb_at_full_power", slow, None)
    # heater reported off, +1 C/min for 33 min
    stuck_on = [sample(i * 5, 300.0 + i * (1 / 12), 290.0, peak=1000.0) for i in range(400)]
    add("temperature_rising_with_the_heater_off", stuck_on, ("stop", "heater_off_rising"))
    # the script's IDLE case: the supervisor only sees RUNNING or PAUSED, so it runs as PAUSED
    stuck_paused = [
        sample(i * 5, 300.0 + i * (1 / 12), 0, state="PAUSED", peak=1000.0) for i in range(400)
    ]
    add("temperature_rising_while_paused", stuck_paused, ("stop", "heater_off_rising"))
    return cases


@pytest.mark.parametrize("rows, expect", _script_cases())
def test_script_cases_are_ported(rows, expect):
    sup = make()
    verdicts = run(sup, rows)
    if expect is None:
        assert all(v == OK for v in verdicts), [v for v in verdicts if v != OK][:1]
        return
    action, rule = expect
    first = next((v for v in verdicts if v != OK), None)
    assert first is not None, "no rule tripped"
    assert (first.action, first.rule) == (action, rule), first
    if action == "plateau":
        # the script stopped here; the supervisor stops after the 30 minute hold
        more = [rows[-1].now + 10 * (k + 1) for k in range(200)]
        tail = run(
            sup, [sample(t, rows[-1].temp, rows[-1].target, duty=1.0, peak=1000.0) for t in more]
        )
        stop = next(v for v in tail if v.action == "stop")
        assert stop.rule == "plateau"
