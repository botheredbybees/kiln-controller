Firing Supervisor
=================

The supervisor watches a firing as a whole and stops it when the kiln behaves in a way
that looks unsafe. It looks at the last few minutes of readings, the heater output and the
profile, which the single-reading limits (`emergency_shutoff_temp`, the heat-rate check)
cannot do. When a rule trips the run is aborted and the `supervisor_stop` alert is sent
(see the [Alerts Guide](alerts.md)). A stall below the target first raises the
`supervisor_plateau` warning, then stops the run if it lasts.

The supervisor is **off by default**. It does nothing until you set `supervisor_enabled = True`
in `config.py` (or in the config editor) and restart the controller.

What each rule does
-------------------

Each rule has a main number in `config.py`. Setting that number to 0 turns the rule off.
A rule that says "for N seconds" starts a timer at the first reading over its limit and
stops the run when a later reading is still over it more than N seconds on.

| rule | what it does |
| --- | --- |
| `peak_ceiling` | Stops the run when two readings in a row are more than `supervisor_peak_margin` above the highest point of the profile. This catches a kiln that is running away whatever the schedule says right now. |
| `over_target_heater_on` | Stops the run when the kiln is `supervisor_over_target_heater_on` above the target with the elements on for `supervisor_over_target_heater_on_seconds`. The controller is asking for heat that the kiln does not need. |
| `over_target` | Stops the run when the kiln is `supervisor_over_target` above the target, with the elements on or off, for `supervisor_over_target_seconds`. It does not count a kiln whose elements are off and which has cooled over the last minute: a warm kiln cooling toward the start of a profile is not a fault, and stopping would achieve nothing. A kiln that is over the target, off and not cooling still stops. |
| `below_target` | Stops the run when the kiln is `supervisor_below_target` below the target for `supervisor_below_target_minutes`. The kiln is not following the profile (failed element, open breaker, thermocouple fault). |
| `rise_rate` | Stops the run when the temperature climbs faster than `supervisor_max_rise_per_minute` over a 60 second stretch of heating. A kiln does not heat that fast unless the reading or the relay is wrong. |
| `heater_off_rising` | Stops the run when the elements have been off for `supervisor_stuck_minutes` and the temperature is still up by `supervisor_stuck_rise` over the last 6 minutes. This is the stuck relay case; the reason says to cut the power at the wall. The kiln coasting upward for a few minutes after the elements switch off does not trip it. |
| `no_rise` | Stops the run when the elements have been on for `supervisor_no_rise_minutes` while the target rises and the kiln has gained less than `supervisor_no_rise_gain` since the run began. |
| `plateau` | When the kiln has been at nearly full power for `supervisor_plateau_minutes`, gained less than `supervisor_plateau_gain` and is still well below the target, it sends the `supervisor_plateau` alert once. If the stall lasts `supervisor_plateau_hold_minutes` more, it stops the run. A kiln that cannot reach the top temperature of a firing is usually worn elements, and waiting does not help. "Well below" is 4 C (7.2 F) under the target; while the controller is catching up (`kiln_must_catch_up`) any reading below the target counts, because the controller freezes the schedule one `pid_control_window` behind the kiln. The stop needs the kiln to be stalled at that moment, not only to have been stalled earlier. |

A short bad reading (a thermocouple glitch that reads far from the last good value for
up to three readings) is ignored by every rule: it neither starts nor clears a timer or a
count. The timers restart when the run is paused or resumed, and the supervisor starts fresh
on every new run.

The `reason` text in the alert and the log is always written in degrees C, whatever
`temp_scale` is. The temperatures the alert carries as numbers are in your display scale.

Settings
--------

All in `config.py`. Temperatures are deltas in your display scale (`temp_scale`); the
comment on each line gives the Celsius value of the default.

| setting | default | meaning |
| --- | --- | --- |
| `supervisor_enabled` | `False` | master switch |
| `supervisor_peak_margin` | 54 | `peak_ceiling` margin (30 C) |
| `supervisor_over_target_heater_on` | 18 | `over_target_heater_on` limit (10 C) |
| `supervisor_over_target_heater_on_seconds` | 20 | and its time |
| `supervisor_over_target` | 45 | `over_target` limit (25 C) |
| `supervisor_over_target_seconds` | 120 | and its time |
| `supervisor_below_target` | 72 | `below_target` limit (40 C) |
| `supervisor_below_target_minutes` | 10 | and its time |
| `supervisor_max_rise_per_minute` | 54 | `rise_rate` limit (30 C per minute) |
| `supervisor_stuck_minutes` | 12 | `heater_off_rising` time with the elements off |
| `supervisor_stuck_rise` | 11 | and the rise it looks for over 6 minutes (6 C) |
| `supervisor_no_rise_minutes` | 10 | `no_rise` heating time |
| `supervisor_no_rise_gain` | 5 | and the gain it expects (3 C) |
| `supervisor_plateau_minutes` | 20 | `plateau` window |
| `supervisor_plateau_gain` | 9 | gain under which the window is a stall (5 C) |
| `supervisor_plateau_hold_minutes` | 30 | how long a stall lasts before the run is stopped |

A setting that is missing from an older `config.py` keeps the value above, so adding the
supervisor to an existing install only needs `supervisor_enabled = True`. Every
number is checked when the supervisor is built at the start of a run. A setting with a
wrong type (a number in quotes, or `True` or `False` where a number belongs) is logged once
and the supervisor is skipped for that run; the oven keeps firing with its existing safety
checks. `supervisor_enabled` must be exactly `True`: `"False"` in quotes, or any other value,
leaves the supervisor off.

Turning it on
-------------

1. Set `supervisor_enabled = True`.
2. Restart the controller, then start a firing. The supervisor is built from the settings at
   the first tick of each run.
3. Watch the first firings with the supervisor on. Raise a limit, or set its main number
   to 0, if a rule stops a firing that was going well.

Limits to know about
--------------------

- The timers run on real elapsed time. A `SimulatedOven` with `sim_speedup_factor` above 1
  misjudges the rate rules, so do not enable the supervisor in a sped-up simulation.
- A running supervisor is not rebuilt when the settings change mid-run. Restart the
  controller or start a new run for a change to take effect.
- The supervisor assumes `kiln_must_catch_up = True`. With it off, the controller lets the
  target fall while the kiln is still hot, so being over the target is normal then. The
  two over-target rules are skipped while the target is falling, and the controller logs a
  warning once per run to say so.
- It only watches a run. When the controller is idle it cannot stop anything. A relay stuck
  closed while idle can only raise the `relay_stuck_on` alert; cut the power at the wall.
- It is software on the same Raspberry Pi as the controller. If the Pi, the thermocouple
  reading or the relay driver fails in a way the readings do not show, it cannot help.
  It does not replace a hardware over-temperature cut-out (a high-limit controller or
  thermal fuse in the element circuit).
