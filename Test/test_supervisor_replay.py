"""Replay real firing logs through the supervisor: none of the healthy stretches may stop.

The fixtures under Test/data/supervisor are excerpts of two recorded runs (columns
elapsed_s, oven_state, temperature_c, target_c, heat, runtime_s; `heat` is seconds on per 2 s
cycle, so duty = heat / 2). Samples are built the way Oven.supervisor_check builds them, and the
supervisor is reset wherever the oven would start afresh: a row that is not RUNNING, or a gap of
more than 10 s in elapsed_s.
"""

import csv
import os

import pytest

from lib.supervisor import Sample, Supervisor, SupervisorConfig

DATA = os.path.join(os.path.dirname(__file__), "data", "supervisor")
HEAT_CYCLE_S = 2.0
MAX_GAP_S = 10.0

BISQUE_PEAK = 1000.0
COOLDOWN_PEAK = 811.0
BISQUE_LOG = "profile-bisque-1000c-test-20261124-100558.csv"
COOLDOWN_LOG = "profile-cooldown-from-811-20261124-195519.csv"

FIXTURES = [
    ("bisque-plateau.csv", BISQUE_PEAK),
    ("bisque-hold-316.csv", BISQUE_PEAK),
    ("bisque-hold-538.csv", BISQUE_PEAK),
    ("cooldown-quartz.csv", COOLDOWN_PEAK),
]


def replay(path, peak):
    """Feed one log through a fresh supervisor and return the list of non-ok verdicts."""
    sup = Supervisor(SupervisorConfig())
    verdicts = []
    last_elapsed = None
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            if not row["temperature_c"]:
                continue  # a notes row
            elapsed = float(row["elapsed_s"])
            if row["oven_state"] != "RUNNING" or (
                last_elapsed is not None and abs(elapsed - last_elapsed) > MAX_GAP_S
            ):
                sup.reset()
            last_elapsed = elapsed
            if row["oven_state"] != "RUNNING":
                continue
            duty = min(max(float(row["heat"]) / HEAT_CYCLE_S, 0.0), 1.0)
            verdict = sup.evaluate(
                Sample(
                    now=elapsed,
                    temp=float(row["temperature_c"]),
                    target=float(row["target_c"]),
                    duty=duty,
                    state="RUNNING",
                    peak=peak,
                )
            )
            if verdict.action != "ok":
                verdicts.append((elapsed, verdict))
    return verdicts


@pytest.mark.parametrize("name, peak", FIXTURES)
def test_replay_fixtures_never_stop(name, peak):
    verdicts = replay(os.path.join(DATA, name), peak)
    stops = [(t, v.rule, v.reason) for t, v in verdicts if v.action == "stop"]
    assert stops == []


def test_replay_fixtures_are_not_empty_and_small():
    for name, _ in FIXTURES:
        path = os.path.join(DATA, name)
        assert os.path.getsize(path) < 200 * 1024
        with open(path, newline="") as f:
            assert len(list(csv.DictReader(f))) > 1000


@pytest.mark.skipif(
    not os.environ.get("KILN_REPLAY_LOG_DIR")
    or not all(
        os.path.exists(os.path.join(os.environ["KILN_REPLAY_LOG_DIR"], name))
        for name in (BISQUE_LOG, COOLDOWN_LOG)
    ),
    reason="set KILN_REPLAY_LOG_DIR to a directory holding the two full recorded logs",
)
@pytest.mark.parametrize("name, peak", [(BISQUE_LOG, BISQUE_PEAK), (COOLDOWN_LOG, COOLDOWN_PEAK)])
def test_replay_full_logs_if_available(name, peak):
    verdicts = replay(os.path.join(os.environ["KILN_REPLAY_LOG_DIR"], name), peak)
    stops = [(t, v.rule, v.reason) for t, v in verdicts if v.action == "stop"]
    assert stops == []
