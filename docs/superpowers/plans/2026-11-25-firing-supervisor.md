# Firing Supervisor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the laptop script's firing-supervision rules into the controller as a testable `lib/supervisor.py` that stops a run (or holds then stops, for a plateau) and alerts.

**Architecture:** A pure module (`Supervisor.evaluate(Sample) -> Verdict`) holds all rule state in rolling windows; it imports no oven, GPIO or config object. `Oven` builds one `Supervisor` per run from `config`, feeds it one sample per RUNNING or PAUSED tick, and acts on the verdict with `abort_run` plus a new alert. Every rule is off unless `supervisor_enabled = True`.

**Tech Stack:** Python 3.11, pytest, ruff, the existing `lib/oven.py`, `lib/alerts.py`, `lib/temp.py` (`delta_to_c`, `to_display`).

**Spec:** `docs/superpowers/specs/2026-11-25-supervisor-design.md` (approved 2026-11-25). The script `tools/kiln-supervisor/profile_monitor.py` (`Rules.check`, `selftest`) is the reference for rule semantics; where the spec and the script differ, the script wins.

## Global Constraints

- Branch `feat/firing-supervisor` from `upstream/main` (226a87e); worktree `~/sourcecode/kiln-controller-wt/supervisor`. No dependency on the open fix PRs.
- Run tests: `UV_PROJECT_ENVIRONMENT=$HOME/sourcecode/kiln-controller/venv PYTHONDONTWRITEBYTECODE=1 uv run --no-sync python -m pytest Test -q -p no:cacheprovider`; lint: `UV_PROJECT_ENVIRONMENT=$HOME/sourcecode/kiln-controller/venv uv run --no-sync ruff check .` Both clean before every commit.
- Commit messages: conventional format (upstream CI checks it), subject at most 72 characters, a body of at most three plain lines saying why, no ticket keys or references to private notes, no filler. Keep the attribution trailers the harness requires. Commit email is already `botheredbybees@gmail.com`.
- Module-internal units are C and seconds; duty is 0 to 1 (the oven's `heat` field is seconds on per `time_step` cycle, so duty = `heat / time_step`).
- The module imports nothing from `lib.oven`, `lib.alerts` or `config`. Settings are read in one place, `SupervisorConfig.from_config`, with `getattr` defaults so an older `config.py` works.
- Every rule is disabled by setting its main number to 0. `supervisor_enabled = False` ships as default.
- A bug in the supervisor must never stop the oven thread: the oven's call to it is wrapped, logs the error once per run, and fails open.
- Rule numbers (script values): peak margin 30 C; over target with heater on 10 C for 20 s; over target at all 25 C for 120 s; below target 40 C for 600 s; rise 30 C/min over a 60 s heating window (at least 6 samples spanning at least 30 s); heater output zero 720 s and a 6 C rise over the last 360 s; no rise: 600 s of heater-on time while the target rises and under 3 C gained since the run began; plateau: a 1,200 s window with mean duty at least 0.9, gain under 5 C, at least 4 C below target, then a 30 minute hold, then stop. Ceiling rule needs 2 consecutive samples; every other rule uses a timer that starts at the first sample over the limit and trips when a later sample is still over it long enough.

## Review Focus

1. **A short low glitch in the reading** (600 C reads 2.85 C for 3 samples, then recovers; seen once on the real kiln). Expected: no stop from the rise-rate, below-target, plateau or no-rise rules. Pinned in Task 2.
2. **Coast after the heater switches off** (temperature rises 8 C in the 4 minutes after output goes to 0, then falls). Expected: no stop from the heater-off rule. Pinned in Task 2.
3. **The first sample of a run reads full duty (2.0 s of 2 s)** before the PID has settled. Expected: no stop and no plateau from it. Pinned in Task 3.
4. **The wall clock steps backwards** (a Pi without a real-time clock re-syncing). Expected: windows reset, no exception, no stop. Pinned in Task 3.
5. **An older `config.py` with none of the new settings, or a rule set to 0.** Expected: no supervisor, or that rule silent; no exception. Pinned in Task 4.

---

### Task 1: Core types and the target-comparison rules

**Files:**
- Create: `lib/supervisor.py`
- Create: `Test/test_supervisor.py`

**Interfaces:**
- Produces (in `lib/supervisor.py`):
  - `@dataclass(frozen=True) class Sample`: `now: float` (seconds), `temp: float` (C), `target: float` (C), `duty: float` (0 to 1), `state: str` (`"RUNNING"` or `"PAUSED"`), `peak: float` (C, highest point of the running profile).
  - `@dataclass(frozen=True) class Verdict`: `action: str` (`"ok"`, `"plateau"` or `"stop"`), `rule: str = ""`, `reason: str = ""`. Module constant `OK = Verdict("ok")`.
  - `@dataclass class SupervisorConfig`: one field per rule number in Global Constraints, in C and seconds, each defaulting to the script value; a value of 0 disables its rule (`peak_margin`, `over_target_heater_on`, `over_target_heater_on_s`, `over_target`, `over_target_s`, `below_target`, `below_target_s`, `max_rise_per_minute`, `stuck_s`, `stuck_rise`, `no_rise_s`, `no_rise_gain`, `plateau_window_s`, `plateau_gain`, `plateau_hold_s`; `plateau_min_duty = 0.9` and `plateau_min_below_target = 4.0` are fields too; and `catch_up_enabled: bool = True`, which is the controller's `kiln_must_catch_up`).
  - `class Supervisor`: `__init__(self, cfg: SupervisorConfig)`, `reset(self) -> None` (clears every window and timer), `evaluate(self, s: Sample) -> Verdict`.
- Rule names (the `rule` string): `peak_ceiling`, `over_target_heater_on`, `over_target`, `below_target`, `rise_rate`, `heater_off_rising`, `no_rise`, `plateau`.

- [ ] **Step 1: Write the failing tests** in `Test/test_supervisor.py`, with a helper `def sample(now, temp, target, duty=0.0, state="RUNNING", peak=850.0) -> Sample` and a helper `def run(sup, rows) -> list[Verdict]`. Tests, with their assertions:
  - `test_peak_ceiling_needs_two_consecutive_samples`: one sample at 881 C (peak 850, margin 30) is `ok`; a second consecutive one gives `stop` with rule `peak_ceiling`; an `ok` sample between them restarts the count.
  - `test_over_target_heater_on_trips_after_20_seconds`: 12 C above target, duty 0.5, at t=0..20 is `ok`; the sample at t=21 is `stop` rule `over_target_heater_on`; the same with duty 0 never trips this rule; a sample back under 10 C resets the timer.
  - `test_over_target_any_trips_after_120_seconds`: 26 C over, duty 0, `ok` to t=120, `stop` rule `over_target` at t=121.
  - `test_below_target_trips_after_600_seconds`: 41 C under, `stop` rule `below_target` at t=601; 39 C under never trips.
  - `test_target_rules_ignore_paused_state`: the over-target and below-target rules never trip in state `PAUSED`.
  - `test_state_change_clears_windows`: 12 C over with heater on for 15 s in RUNNING, then one PAUSED sample, then RUNNING again: the 20 s timer starts again.
  - `test_over_target_rules_skip_a_falling_target_without_catch_up`: with `catch_up_enabled=False` and a target that fell more than 0.1 C in the last 60 s, 30 C over target with the heater on for 300 s never trips rules `over_target_heater_on` or `over_target`; with `catch_up_enabled=True` the same input trips.
  - `test_ceiling_and_target_rules_are_off_when_zero`: with `peak_margin=0`, `over_target_heater_on=0`, `over_target=0`, `below_target=0` nothing trips.
- [ ] **Step 2: Run** `pytest Test/test_supervisor.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** the dataclasses, `Supervisor.reset`, and `evaluate` for the four rules above. `evaluate` records `last_state`; if `s.state` differs from it, call `reset()` first. One timer attribute per rule (`None` when not running). Return the first tripping rule's `Verdict("stop", rule, reason)`; `reason` is one plain sentence with the numbers (for example "881.0 C is more than 30 C above the profile's highest point (850 C)").
- [ ] **Step 4: Run** the tests and `ruff`. Expected: PASS, clean.
- [ ] **Step 5: Commit** `feat: add the supervisor module with the target-comparison rules`.

### Task 2: Heater and rate rules (heater-off rise, rise rate, no rise)

**Files:**
- Modify: `lib/supervisor.py`
- Modify: `Test/test_supervisor.py`

**Interfaces:**
- Consumes: Task 1's `Sample`, `Verdict`, `SupervisorConfig`, `Supervisor.evaluate`.
- Produces: `evaluate` also returns `stop` with rules `rise_rate`, `heater_off_rising`, `no_rise`.

- [ ] **Step 1: Write the failing tests:**
  - `test_rise_rate_trips_over_30_c_per_minute`: duty 1.0, 2 s samples rising 1.2 C per sample (36 C/min) for 60 s gives `stop` rule `rise_rate`; 0.8 C per sample (24 C/min) never does; the window is cleared on a sample with duty 0.
  - `test_rise_rate_needs_six_samples_spanning_30_seconds`: three samples 2 s apart rising 10 C each never trip.
  - `test_heater_off_rising_trips`: duty 0 for 721 s with the temperature 7 C higher than 360 s earlier gives `stop` rule `heater_off_rising`, and the reason contains `cut the power at the wall`; duty 0 for 721 s with a 5 C rise does not; any sample with duty above 0.001 restarts the 720 s timer.
  - `test_coast_after_switch_off_does_not_trip` (Review Focus 2): duty 0 from t=0, temperature rises 8 C over the first 240 s then falls 0.1 C per 2 s for 30 minutes: every verdict is `ok`.
  - `test_no_rise_trips_after_600_seconds_of_heating`: target rising 0.1 C per 2 s, duty 1.0, temperature flat for 601 s of heater-on time gives `stop` rule `no_rise`; a gain of 3 C or more since the first sample does not; heating time does not accumulate while the target is flat.
  - `test_short_low_glitch_does_not_stop` (Review Focus 1): a ramp from 580 C at 5 C/min with duty 0.8, three consecutive samples at 2.85 C in the middle, then back on the ramp: every verdict is `ok` (the implementation must not compute a rate or a gain across the glitch; ignore a sample that differs from the previous one by more than 100 C for rate, gain and below-target purposes, counting at most 3 such samples in a row).
- [ ] **Step 2: Run** the new tests. Expected: FAIL.
- [ ] **Step 3: Implement** the three rules inside `evaluate`, with rolling histories pruned by time: a 60 s heating history (cleared when duty is 0), a 60 s target history for "target rising" (target rose more than 0.1 C), a sample history of the last 390 s for rule 4. Keep per-tick work small (this runs on a 700 MHz Pi).
- [ ] **Step 4: Run** the full file and `ruff`. Expected: PASS, clean.
- [ ] **Step 5: Commit** `feat(supervisor): add the heater-off rise, rise rate and no-rise rules`.

### Task 3: The plateau rule (hold, then stop) and clock safety

**Files:**
- Modify: `lib/supervisor.py`
- Modify: `Test/test_supervisor.py`

**Interfaces:**
- Consumes: Task 2's `Supervisor`.
- Produces: `evaluate` returns `Verdict("plateau", "plateau", reason)` exactly once when a plateau is first detected, then `ok` while it persists, then `Verdict("stop", "plateau", reason)` once `plateau_hold_s` has passed since detection; a gain of `plateau_gain` or more over the window clears the state so a later plateau reports again.

- [ ] **Step 1: Write the failing tests:**
  - `test_plateau_detected_after_20_minutes_at_full_power`: duty 1.0, temperature flat at 864 C, target 870 C, samples every 2 s: the first `plateau` verdict arrives between t=1,140 and t=1,200, and it arrives only once.
  - `test_plateau_stops_after_the_hold`: continuing the above, the verdict is `ok` until 30 minutes after detection and `stop` rule `plateau` at the first sample 1,800 s after it.
  - `test_plateau_clears_when_the_kiln_gains_5_c`: a gain of 5 C over the window before the hold ends clears it; no stop follows; a second plateau later reports `plateau` again.
  - `test_not_a_plateau_when_close_to_target`: temperature 2 C below target at full power for an hour is `ok`.
  - `test_not_a_plateau_at_low_duty`: duty 0.5 for an hour with a flat temperature 20 C below target is `ok` for this rule.
  - `test_first_sample_at_full_duty_is_harmless` (Review Focus 3): a run whose first sample has duty 1.0 and whose later samples are a normal ramp never gives `plateau` or `stop`.
  - `test_clock_stepping_backwards_resets_windows` (Review Focus 4): after 600 s of samples, a sample with `now` 3,600 s earlier returns `ok` without raising, and 12 C-over timers start again from it.
- [ ] **Step 2: Run** the new tests. Expected: FAIL.
- [ ] **Step 3: Implement** the plateau window (samples of the last `plateau_window_s` plus 30 s) and `plateau_since`, and in `evaluate` the backwards-clock guard (`s.now < last_now` calls `reset()` and returns `OK`).
- [ ] **Step 4: Run** the full file and `ruff`. Expected: PASS, clean.
- [ ] **Step 5: Commit** `feat(supervisor): add the plateau rule with a hold before stopping`.

### Task 4: Settings, config conversion and alert definitions

**Files:**
- Modify: `config.py` (a new block after the existing safety-detector settings)
- Modify: `lib/supervisor.py` (add `SupervisorConfig.from_config`)
- Modify: `lib/alerts.py` (two entries in `ALERTS`)
- Modify: `docs/alerts.md`, `Test/test_config.py`, `Test/test_supervisor.py`, any alert test that counts the registry

**Interfaces:**
- Consumes: `SupervisorConfig` (Task 1).
- Produces: `SupervisorConfig.from_config(cfg) -> SupervisorConfig | None` (a classmethod taking the config module; `None` when `supervisor_enabled` is missing or false; temperature deltas converted with `lib.temp.delta_to_c`, minutes to seconds; every setting read with `getattr(cfg, name, <script value in display scale>)`; `catch_up_enabled = getattr(cfg, "kiln_must_catch_up", True)`). Alerts `supervisor_stop` (CRITICAL, placed after `heat_rate_too_low`) and `supervisor_plateau` (WARNING, placed after `catch_up_stalled`); the existing `relay_stuck_on` description gains the sentence "If the kiln keeps heating, cut the power at the wall."
- Settings in `config.py`, shipped in the repo's default scale (F) with the C value in each comment: `supervisor_enabled = False`; `supervisor_peak_margin = 54` (30 C); `supervisor_over_target_heater_on = 18` (10 C); `supervisor_over_target_heater_on_seconds = 20`; `supervisor_over_target = 45` (25 C); `supervisor_over_target_seconds = 120`; `supervisor_below_target = 72` (40 C); `supervisor_below_target_minutes = 10`; `supervisor_max_rise_per_minute = 54` (30 C/min); `supervisor_stuck_minutes = 12`; `supervisor_stuck_rise = 11` (6 C); `supervisor_no_rise_minutes = 10`; `supervisor_no_rise_gain = 5` (3 C); `supervisor_plateau_minutes = 20`; `supervisor_plateau_gain = 9` (5 C); `supervisor_plateau_hold_minutes = 30`. Comments say each is a delta in the display scale and that 0 turns the rule off.

- [ ] **Step 1: Write the failing tests:**
  - `test_from_config_returns_none_when_disabled_or_missing`: a `types.SimpleNamespace()` with nothing set, and one with `supervisor_enabled=False`, both give `None` (Review Focus 5).
  - `test_from_config_converts_display_deltas_to_celsius`: with `temp_scale = "f"` and `supervisor_peak_margin = 54`, the result has `peak_margin == pytest.approx(30.0)`; `supervisor_below_target_minutes = 10` gives `below_target_s == 600`.
  - `test_from_config_zero_disables_a_rule`: `supervisor_over_target = 0` gives a config whose over-target rule never trips (evaluate 26 C over for 300 s).
  - In `Test/test_config.py`: `test_supervisor_settings_ship_off_and_in_display_scale` asserts `config.supervisor_enabled is False` and the settings above.
  - In the alert tests: the registry still sorted by criticality; `supervisor_stop` is CRITICAL, `supervisor_plateau` is WARNING.
- [ ] **Step 2: Run** the new tests. Expected: FAIL.
- [ ] **Step 3: Implement** `from_config` (reading the display scale through `lib.temp`, as the oven does), add the settings block to `config.py`, add the two alerts and the `relay_stuck_on` sentence to `lib/alerts.py`, and document the new alert ids in `docs/alerts.md`.
- [ ] **Step 4: Run** the full suite and `ruff`. Expected: PASS, clean.
- [ ] **Step 5: Commit** `feat(supervisor): add the settings and the supervisor alerts`.

### Task 5: Wire the supervisor into the oven

**Files:**
- Modify: `lib/oven.py` (`Oven.reset`, `Oven.run_profile`, the PAUSED and RUNNING branches of `_run_once`, one new method)
- Create: `docs/supervisor.md`
- Modify: `Test/test_oven.py` (or a new `Test/test_oven_supervisor.py` if that keeps the file focused)

**Interfaces:**
- Consumes: `Supervisor`, `Sample`, `SupervisorConfig.from_config` (Tasks 1 to 4); `Oven._emit`, `Oven.abort_run(reason=...)`, `Oven._current_temp()`, `Oven.heat`, `Oven.time_step`, `Oven.profile.data`.
- Produces: `Oven.supervisor_check(self) -> None`, called right after `reset_if_emergency()` in both the PAUSED and RUNNING branches of `_run_once`. State `self._supervisor` (`None` until built for a run; reset to `None` in `reset()` and `run_profile`), `self._supervisor_failed` (bool, once-per-run error log).

- [ ] **Step 1: Write the failing tests** (bare oven with a fake board and a recording alert sink, as in `Test/test_alert_detection.py`; set `config.supervisor_enabled` through `monkeypatch`):
  - `test_supervisor_disabled_by_default_never_aborts`: 12 C over target with the heater on for 300 s of faked time in RUNNING leaves the oven RUNNING.
  - `test_supervisor_stop_aborts_the_run_and_alerts`: enabled; the over-target-with-heater-on rule trips; afterwards `oven.state == "IDLE"` and the sink received `supervisor_stop` (context has `rule` and `reason`) and `run_aborted`.
  - `test_supervisor_plateau_alerts_once_and_keeps_running`: a plateau verdict emits `supervisor_plateau` once and the oven stays RUNNING.
  - `test_supervisor_error_fails_open`: a `Supervisor.evaluate` that raises leaves the oven RUNNING, logs one error, and the existing `reset_if_emergency` still aborts at `emergency_shutoff_temp`.
  - `test_supervisor_warns_once_when_catch_up_is_off`: with `config.kiln_must_catch_up = False` the build logs one warning (caplog) and not a second on the next tick.
  - `test_supervisor_builds_one_per_run_and_resets_with_the_run`: a second `run_profile` gets a fresh `Supervisor` (windows empty).
  - `test_supervisor_sample_uses_profile_peak_and_duty`: the `Sample` passed in has `peak` equal to the profile's highest point (converted to C) and `duty == oven.heat / oven.time_step` clamped to 0..1.
- [ ] **Step 2: Run** the new tests. Expected: FAIL.
- [ ] **Step 3: Implement `Oven.supervisor_check`** and the two call sites. It builds the `Supervisor` lazily with `SupervisorConfig.from_config(config)` (returning when that is `None`), builds a `Sample` (`now=time.time()`, `temp=self._current_temp()`, `target=self.target`, duty, `state=self.state`, peak), and acts on the verdict: `plateau` emits `supervisor_plateau` (temperature, target, minutes held so far); `stop` emits `supervisor_stop` (rule, reason, temperature, profile name) then calls `self.abort_run(reason="supervisor: " + rule)`. When the built config has `catch_up_enabled` false, log one warning that the over-target rules are skipped on falling targets. Wrap everything except `abort_run` in try/except Exception.
- [ ] **Step 4: Write `docs/supervisor.md`**: what each rule does in two sentences, the settings, that every rule is off by default and how to turn them on, that it assumes `kiln_must_catch_up = True`, that a stuck relay in IDLE can only alert, and that it does not replace a hardware over-temperature cut-out.
- [ ] **Step 5: Run** the full suite and `ruff`. Expected: PASS, clean.
- [ ] **Step 6: Commit** `feat: stop a firing when the supervisor trips`.

### Task 6: Replay test against the real logs and the ported script cases

**Files:**
- Create: `Test/test_supervisor_replay.py`
- Create: `Test/data/supervisor/bisque-plateau.csv`, `Test/data/supervisor/bisque-holds.csv`, `Test/data/supervisor/cooldown-quartz.csv` (excerpts)
- Modify: `Test/test_supervisor.py` (the ported cases)

**Interfaces:**
- Consumes: `Supervisor`, `Sample`, `SupervisorConfig` (the script-valued defaults).

- [ ] **Step 1: Build the fixtures** with a one-off script (not committed): from `~/backups/kiln-profiling/profile-bisque-1000c-test-20261124-100558.csv` take the last 45 minutes before the stop (the plateau), the 316 C and 538 C holds with 20 minutes either side; from `profile-cooldown-from-811-20261124-195519.csv` take the 20:50 to 21:50 stretch through 573 C. Keep columns `elapsed_s,oven_state,temperature_c,target_c,heat,runtime_s` unchanged (`heat` stays seconds on per 2 s cycle). Each file under 200 KB.
- [ ] **Step 2: Write the tests:**
  - `test_replay_fixtures_never_stop`: for each fixture, build `Sample`s from RUNNING rows (`duty = heat / 2.0`, `peak = 1000.0` for the bisque files and `811.0` for the cool-down), call `reset()` wherever the row's state is not RUNNING or `elapsed_s` jumps by more than 10 s, and assert no verdict has `action == "stop"`.
  - `test_replay_full_logs_if_available`: skipped unless env var `KILN_REPLAY_LOG_DIR` points at a directory containing the two full log files; the same assertion over the whole files.
  - `test_script_cases_are_ported`: parametrised over the cases in `tools/kiln-supervisor/profile_monitor.py` `selftest()` (lines 162 to 230) that apply to rules in this module; each constructed sequence is rebuilt as `Sample`s and asserts the same trip (by rule name) or the same silence as the script. Cases for rules not ported (feed silence, absolute ceiling, run length) are listed in a comment as not applicable.
- [ ] **Step 3: Run** the tests. Expected: PASS. If a fixture trips a rule, that is a threshold or semantic bug in the module: fix the module (compare with `Rules.check`), not the fixture.
- [ ] **Step 4: Run** the full suite and `ruff`. Expected: PASS, clean.
- [ ] **Step 5: Commit** `test: replay real firing logs through the supervisor`.

### Task 7: Final check

- [ ] **Step 1:** Run the full suite, `ruff`, and `git log --format='%an <%ae>%n%s%n%b' upstream/main..HEAD`; confirm every author is `botheredbybees@gmail.com`, every subject is conventional and under 73 characters, and no body mentions a ticket key or private notes.
- [ ] **Step 2:** Read `git diff upstream/main...HEAD --stat`; the change should touch `lib/supervisor.py`, `lib/oven.py`, `lib/alerts.py`, `config.py`, `docs/`, and tests only.
- [ ] **Step 3:** Do not push or open a PR; report to the owner.
