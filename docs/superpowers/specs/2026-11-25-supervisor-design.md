# Firing supervisor: design

Status: draft for approval (2026-11-25). Not implemented.

## Intent and why

The controller aborts a run for only a few faults (one global `emergency_shutoff_temp`, a high thermocouple
error rate, stale readings, and a slow heat rate while the target is rising). Everything else it notices, it only
reports: `relay_stuck_on`, `temp_implausible` and `catch_up_stalled` send alerts and change nothing.

During the first long firings of this fork on a small kiln (about 2.4 kW, tops out near 865 C), the faults that
mattered were caught by a laptop script (`tools/kiln-supervisor/profile_monitor.py`) that watched the status feed
and sent Stop. That is the wrong place for it: the script dies with the laptop's session, and it cannot act when
the Pi is off the network (that happened on the first heated test). The owner's words: the controller should be
taking care of this, not a harness around it.

This design moves the script's rules into the controller, so a firing is protected by code running on the Pi itself.
The script's thresholds were chosen from real firings (17,417 readings in a 9 h 45 min run to 873 C, then a 9 h
controlled cool-down) and stay the reference numbers.

## Goals

- A new module, `lib/supervisor.py`, holds the rules. Each tick the oven gives it one sample (time, temperature,
  target, heater duty, state, profile peak) and it answers "fine", "hold" or "stop, with this rule and reason".
  The oven acts on the answer. The module imports no oven, no GPIO and no config object, so tests feed it plain data.
- Rules (numbers are the script's, in `config.py` with comments, temperatures in the display scale like the existing
  settings; each rule is disabled by setting its main number to 0):
  1. **Profile ceiling.** Stop when the temperature exceeds the running profile's highest point plus
     `supervisor_peak_margin` (script: 30 C) for 2 consecutive samples. `emergency_shutoff_temp` stays as the
     global limit.
  2. **Over target, heater on.** Stop when the kiln is more than `supervisor_over_target_heater_on` above target
     (script: 10 C) for 20 s while the heater output is above zero.
  3. **Over target, any state.** Stop when more than `supervisor_over_target_any` above target (script: 25 C) for
     120 s.
  4. **Rise while the heater is off.** Stop when the heater output has been zero for `supervisor_stuck_minutes`
     (script: 12 min) and the temperature has risen by `supervisor_stuck_rise` (script: 6 C) over the last 6
     minutes. The message says to cut the power at the wall: software cannot open a welded relay. This gives the
     existing `relay_stuck_on` alert (25 C in 10 min, too slow) an action with a faster test.
  5. **Rise too fast.** Stop when the temperature rises faster than `supervisor_max_rise_per_minute` (script:
     30 C/min) while the heater is on.
  6. **Plateau (the kiln cannot reach its target).** Detect: heater output at 90 % or more for 20 minutes, gaining
     under 5 C, and at least 4 C below target. Response: **hold, then stop.** Keep heating at the current target
     (the schedule is already frozen by `kiln_must_catch_up`) for `supervisor_plateau_hold_minutes` more
     (default 30), so a kiln that is only slow can recover; if the plateau persists to the end of the hold, stop.
     A gain of 5 C or more clears the plateau. The owner's reason for stopping after a hold: make sure there is no
     way the firing can progress past a kiln that cannot follow its schedule.
  7. **Far below target.** Stop when more than `supervisor_below_target` below target (script: 40 C) for 10
     minutes. Off in the fork's config if it overlaps rule 6 in practice (builder to check against the logs).
  8. **No rise while heating.** Stop when the heater has been on for `supervisor_no_rise_minutes` (script: 10 min of
     heater-on time while the target is rising) and the kiln has gained under 3 C since the run began (a failed
     element or an open relay). The existing heat-rate check does the same job more slowly (5 C/h over 22.5 min).
     Off the replay test's evidence, the builder may drop this rule if it never adds anything.
  The script's last rule, stop if a run lasts more than 30 minutes past the profile's length, is **not** ported: a
  cool-down legitimately ran about 5 hours past its profile because the kiln cools more slowly than the profile.
- "Stop" means the existing `abort_run(reason=...)`, which turns the heater off and ends the run, plus a new alert
  `supervisor_stop` carrying the rule name, the numbers and, for rule 4, the "cut the power at the wall" line, so
  existing alert delivery covers it.
- Heater duty is computed by the oven as the heat-on fraction of the cycle (0 to 1). The status feed's `heat`
  field is seconds on per cycle, not a fraction; the script was wrong about this once.
- Every rule is off by default in the shipped `config.py` (so an upstream user's firings do not change on upgrade),
  with a master switch `supervisor_enabled`. The fork's own config turns them on.
- A replay test: the real logs (the 9 h 45 min bisque run and the 9 h cool-down) are fed through the module and
  must produce no stop (the bisque log ends where the script stopped the run; replaying it showed the plateau rule gives no
  plateau verdict on it: the kiln was still gaining slowly until the profile swaps, and after them the target was within
  4 C of the temperature, so the rule is quiet, not wrong, and nothing on real data yet shows it catching a plateau), and a short excerpt is committed as a fixture. The script's 24 self-test
  cases are ported.

## Non-goals

- A hardware over-temperature cut-out, or an observer for a hung Pi or lost network. Those stay outside software.
- The schedule features the script also worked around: starting a descending profile from the kiln's current
  temperature, swapping profiles without resetting the PID, and a controlled cool-down. Separate project.
- Replacing `emergency_shutoff_temp`, the heat-rate check or the thermocouple checks. They stay as they are.
- Changing the existing alerts (`relay_stuck_on` etc.). They keep alerting; this adds actions beside them.

## Anti-goals (a failure even if the code works)

- A stop during a legitimate firing. Holds overshoot by 5 to 6 C and the kiln trails ramps by 4 to 5 C in normal
  firings; the thresholds above were set around that. The replay test guards it.
- The supervisor itself raising an exception into the control loop. A bug in it must log and fail open to the
  existing checks, never stop the oven thread.
- A rule that can only be turned off by editing code.

## Constraints

- Python 3.11, runs on an original Raspberry Pi Model B (single 700 MHz core) at one tick per 2 s: the module
  must be cheap (rolling windows by time, no per-tick list growth).
- Config values in the display scale, converted with the existing `delta_to_c` and `to_c` helpers; the oven's
  internal unit is C. Read new settings with `getattr` defaults so an older `config.py` keeps working.
- Written to be sent upstream (jbruce12000/kiln-controller): based on upstream `main`, conventional commits,
  tests in `Test/`, `ruff` clean, the new settings documented in `config.py` and `docs/`. No reference to private
  notes in any tracked file.

## Approaches

- **A. More checks beside `reset_if_emergency`**, one method and one set of state per rule. Matches the current
  style and could go up as small PRs, but `lib/oven.py` already has about 1,400 lines and the rules would need a
  live oven to test.
- **B. A `lib/supervisor.py` module** (chosen by the owner, 2026-11-25). Testable without an oven, all numbers
  in one config block, replayable against real logs, and the plateau "hold then stop" state machine lives in one
  place. Cost: a new module for upstream to accept, so one larger PR with every rule off by default.
- **C. Run the laptop script on the Pi** as a second process. Rejected: it could die on its own, and it is still
  a harness.

## What the owner decided

- Approach B; stop-style rules first, schedule features later.
- Faults stop and alert; the plateau holds then stops.
- If upstream does not take it, that is their loss; the fork keeps it.

## Left to the builder

- Names of the config settings, the shape of the sample and of the answer, and where in the tick the oven calls it.
- How the oven gives the supervisor the profile's peak and the heater duty.
- Fixture size and format for the replay test.
- How "hold" is carried when `kiln_must_catch_up` already freezes the schedule (the hold is time spent in the
  plateau state; the oven keeps doing what it does).
- Whether to split the upstream PR (module and tests; wiring and config; docs).

## Rule semantics

`tools/kiln-supervisor/profile_monitor.py` is the reference. Where this document and the script differ, the script
wins and this document is wrong. The script's `Rules.check` defines windows by time, not sample count: a rule that
says "for 20 s" means the first sample over the limit started a timer and the rule trips when a later sample is
still over it 20 s on. The exceptions are the ceiling rules, which need 2 consecutive samples (here, 2 oven ticks of
2 s). The rate rule (5) uses the heating samples of the last 60 s (at least 6 samples spanning at least 30 s). The
plateau rule (6) looks at a 1,200 s window: mean heater duty at least 0.9, gain over the window under 5 C, and at
least 4 C below target at the end. Rule 4's "heater off" means output zero continuously for the whole 12 minutes.

## Decisions made after the reviewer's questions (builder's calls, marked so they can be changed)

- **States.** The supervisor runs on RUNNING and PAUSED ticks. Its windows are cleared on any state change, on the
  start of a run, and on an automatic restart, so a restart cannot trip a rule from stale samples. Rules 2, 3 and 7
  (target comparisons) apply in RUNNING only, as in the script. Rule 4 in IDLE stays with the existing
  `check_safety_detectors` path: `abort_run` does nothing in IDLE, so there it can only alert, and the alert text
  gains the "cut the power at the wall" line.
- **`kiln_must_catch_up`.** The rules assume the default (`kiln_must_catch_up = True`), which freezes the schedule
  when the kiln is outside `pid_control_window` of the target. On a cool-down the kiln hovered about 5 C above target
  with the schedule held back; it never sat 25 C above. If a user sets it to False, rules 2 and 3 are skipped while
  the target is falling and the module logs a warning once at start.
- **The cool-down replay is valid.** The target in the logged cool-down is the controller's own output; only the
  starting profile was generated by the script. The replay asserts: no stop in the bisque run (9 h 45 min, which ends
  where the script stopped it) and none in the cool-down (9 h), then a set of constructed cases that must stop (the script's 24 cases).
- **Logs.** `~/backups/kiln-profiling/profile-bisque-1000c-test-20261124-100558.csv` and
  `profile-cooldown-from-811-20261124-195519.csv` have columns `wall_time, elapsed_s, oven_state, temperature_c,
  target_c, heat, runtime_s, totaltime_s, temp_errors, note`. `heat` is seconds on per 2 s cycle (divide by 2).
  The profile peak comes from the matching profile (850 and 1000 C for the bisque test, 811 C for the cool-down).
  A trimmed excerpt (the plateau, a hold, a ramp) is committed as a fixture; the full logs stay out of the repo.
- **Base branch.** Upstream `main`, with no dependency on the open fix PRs (the wiring only calls `abort_run`). The
  working branch is merged into the fork's integration branch for use on the kiln; any conflicts with the open PRs
  are settled there.
- **Plateau state.** The 20-minute detection window and the 30-minute hold are sequential: the hold starts when the
  plateau is detected. Entering the plateau emits a warning alert (`supervisor_plateau`) and a stop emits
  `supervisor_stop`. A gain of 5 C or more clears the state and re-arms it. If the existing heat-rate check or
  another emergency aborts first, that wins; the supervisor does not suppress existing checks.
- **Turning it on in the fork.** Editing the Pi's live `config.py` is a separate deployment step after the code
  is merged; it is not part of this work.

## Findings after implementation (2026-11-25)

- The replay of the real bisque log produced no plateau verdict and no stop (17,414 rows), and the cool-down none (15,876
  rows). The plateau rule is therefore tested only on constructed cases.
- The whole-branch review added: all numeric settings are coerced at build time (a bad value skips the supervisor for the run);
  the plateau's below-target leg also counts "catching up and below target" so it works with a small `pid_control_window`;
  the plateau stop needs the current sample to be stalled; `over_target` ignores samples with the heater off and the
  temperature falling; the supervisor clock is monotonic.
