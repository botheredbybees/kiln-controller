# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal fork of [jbruce12000/kiln-controller](https://github.com/jbruce12000/kiln-controller) (the maintained 2023+ rewrite of picoReflow): a web-controlled kiln controller for a Raspberry Pi. It drives a **mains heater through a solid-state relay**, so treat changes to the control loop and the safety limits as safety-critical.

- `origin` is the fork (`botheredbybees/kiln-controller`), `upstream` is `jbruce12000/kiln-controller`. Both default branches are `main`. Upstream's `master` branch is the legacy pre-rewrite code; do not base work on it.
- Keep `main` close to upstream so pulls stay clean: `git fetch upstream && git merge upstream/main` (or `gh repo sync botheredbybees/kiln-controller --branch main` for the fork on GitHub).
- Settings for this particular kiln (wiring, unit conversions, ported profiles) live in `docs/my-kiln.md`, deliberately **not** in the tracked `config.py`.
- The `add-web-interface-dev-guide` branch on `origin` holds three unmerged documentation commits by the fork's owner.

## Commands

```
make dev-setup                        # venv/ + uv sync (installs uv if missing)
UV_PROJECT_ENVIRONMENT=venv uv sync   # just the dependencies, into the gitignored venv/
uv run pytest Test -q                 # full suite (762 tests, about 5 s)
uv run pytest Test/test_Profile.py -q # one file; add -k name for one test
uv run ruff check .                   # lint (Pyflakes rules only, line length 100)
uv run ruff format .                  # formatter
uv run python kiln-controller.py      # run the server
```

- Without `UV_PROJECT_ENVIRONMENT=venv`, `uv` creates `.venv/`, which is not gitignored (`venv/*` is).
- The shipped `config.py` has `simulate = True`, so the server runs on any machine with a simulated oven and no GPIO. It listens on `listening_port` (9099); `docs/api.md` still quotes 8081 in its examples.
- Python 3.11 or newer; 3.11 and 3.12 are the tested versions because `lgpio` (via `rpi-lgpio`) only ships wheels for those.
- **CI enforces Conventional Commits** (`feat:`, `fix:`, `docs:`, ...) on every push and PR. CI also runs `ruff format --check`, `ruff check .` and `pytest --doctest-modules Test`, so run `ruff format .` before committing Python changes.
- The version lives in two places that a test keeps in sync: `lib/version.py` and `pyproject.toml`. Releases are tags `vX.Y.Z`; never re-tag.

## Architecture

- **`kiln-controller.py`** (one 2.6k-line file): the bottle app on a gevent `WSGIServer` with `WebSocketHandler`. It serves `public/`, a JSON API (`POST /api` with `{"cmd": "run"|"stop"|"memo"|...}`, `/api/stats`, `/api/firings*`, `/api/alerts`, `/api/tune`, `/api/profiles/remote*`, `/api/config/editor`), and four WebSockets: `/control`, `/storage` (profiles), `/config`, `/status`.
- **`lib/oven.py`** holds the control logic. `Oven` is a thread; `RealOven` and `SimulatedOven` subclass it. `Board`/`Output`/`TempSensor` wrap the hardware via Adafruit blinka (`RealBoard` or `SimulatedBoard`; `Max31855` or `Max31856` sensors), `Profile` turns `[[seconds, temp], ...]` into targets, and `PID` is the controller. Emergency shutoff, the heat-rate check and thermocouple error handling live here, driven by the `emergency_*` and `ignore_*` settings in `config.py`.
- **`lib/ovenWatcher.py`** broadcasts oven state to the `/status` sockets. Other modules in `lib/`: `scheduler.py` (start a firing at a set time), `alerts.py` (alert registry, webhooks), `firing_db.py` (SQLite firing history under `db/`, gitignored), `tuner.py` (PID auto-tune behind `POST /api/tune`; `kiln-tuner.py` is a separate Ziegler-Nichols script that does not use it), `mqttout.py` (optional telemetry), `temp.py` (unit conversions).
- **Frontend**: `public/index.html` and `public/assets/js/`. The tests execute that JavaScript through `quickjs` (`Test/test_frontend_*.py`).
- The automatic-restart state is `state.json` next to `config.py` and `storage/schedules.json`; both are gitignored.

## Things that bite

- **Units.** The server works internally in Celsius, but every temperature in `config.py` is read in the `temp_scale` you choose, and the shipped values are Fahrenheit. Changing `temp_scale` without converting `emergency_shutoff_temp`, `emergency_heat_rate`, `pid_control_window` and the alert thresholds silently loosens the safety limits.
- **Profile units.** A profile JSON without `"temp_units": "c"` is treated as Fahrenheit (`kiln-controller.py`, `normalize_temp_units`). Profile names must match `^[A-Za-z0-9._-]+$` and the file must be `<name>.json`.
- **`config.py` is tracked and is also rewritten by the web UI** (`/api/config/editor`), so local edits on a Pi show up as a diff against upstream.
- **The shipped SPI pins** (`spi_sclk = D17`, `spi_miso = D27`, `spi_cs = D22`) are not this kiln's wiring; see `docs/my-kiln.md`.
- **Display-scale settings.** `throttle_below_temp` and `throttle_percent` limit heating at low temperatures and are also in the display scale, so convert `throttle_below_temp` with the others.

## Known defects (reviewed 2026-11-22, unfixed upstream)

- **A dead sensor thread goes unnoticed.** `TempSensorReal.run` has no `try/except`, and `get_temperature` only catches `ThermocoupleError`. Any other exception (an `OSError` from the SPI layer, say) kills the thread. `temperature()` then returns the last good median forever, the error tracker still reads 0 %, and the over-temperature and error-rate checks are blind. Reproduced with a stub sensor. The heat-rate check only covers rising segments, and `kiln_must_catch_up` holds the schedule while the frozen reading sits below target, so a hold segment would keep the elements on. There is no staleness check on readings.
- **No authentication, and the config editor executes code.** The server binds `0.0.0.0` (hardcoded in `main()`) and no endpoint checks credentials or Origin. `POST /api/config/editor` only runs `compile()` on the body, writes it to `config.py` and `importlib.reload`s it, so anyone who can reach the port gets code execution as the service user, and can also loosen every emergency limit.
- **The safety detectors only alert.** `relay_stuck_on` and `temp_implausible` (`check_safety_detectors`) emit alerts and take no action. `catch_up_stalled` is the same.
- **The autotuner is outside the safety checks.** In state `TUNING`, `Oven._run_once` does nothing, so the emergency checks do not run. `abort_run` and `run_profile` never touch the tuner, so the main Stop button sets the oven to IDLE and the tuner's next loop switches the relay back on. The tuner has no sensor-fault check or time limit, `/api/tune` only runs `float()` on the target, and `_apply_pid` rewrites `config.py` unreviewed. `kiln-tuner.py` has no checks at all.
- **A run can use the wrong temperature scale.** `lib/temp.py` lowercases `temp_scale`, but `kiln-controller.py` compares to `"f"` exactly (lines around 2063, 2533, 2557), so `temp_scale = "F"` skips the profile conversion. `Oven._load_profile_by_name` (the resume fallback) does no `temp_units` handling, and `temp_units: "C"` is read as Fahrenheit. Keep `temp_scale` lowercase.
- **Stop and Run are not safe against races.** `save_state` uses one shared tmp file with no lock, so a save that starts before a STOP can land after it and the IDLE branch then resumes the stopped firing. `POST /api` run, the `/control` RUN message and `Oven.run_profile` do not check the oven state.
- **Unattended starts.** `state_file_is_old` accepts a negative age (a Pi with no real-time clock after an outage), `Scheduler.pending` has no lateness limit, a chained firing starts after any end reason including an abort, and `schedule_tick` stops for good on one exception.
- **Community profiles.** Remote `name` and `category` reach inline JavaScript unescaped (`public/assets/js/kiln-controller.js`, the Preview and Install buttons), the path check only tests `".."`, and an import overwrites a local profile of the same name. `import_profile` and the editor do not validate profile data, and one malformed file makes `get_profiles` raise and kills the `/storage` handler.
- **The web page can mislead.** It keeps showing the last temperature and "Running" after the `/status` socket drops, edit mode sets `state = "EDIT"` and freezes the live status, the trash button deletes `selected_profile_name` (which can be the running profile) instead of the one being edited, and the Overview tab has no Stop button.
- **Alerts.** Critical alerts are rate-limited per id for 300 s, webhook URLs are returned by `GET /api/alerts` and logged, and MQTT stays dead if the broker was down at the first connect. Alert delivery does not block the control thread.
- **No exit handler or watchdog** (open upstream as issue #189). The controller does not force the relay off when it exits. Whether the pin stays high after the process dies depends on the GPIO backend (`rpi-lgpio` has no `atexit` hook; whether the kernel releases the line on exit was not verified on hardware), so do not assume the heater is off after a crash or `kill -9`.
