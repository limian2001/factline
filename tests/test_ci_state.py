"""Tests for the CI verdict used to gate deploys.

This logic decides whether a commit reaches production, and it was wrong once in
a way nothing caught: it read the commit Status API, which GitHub Actions never
writes to, so the verdict was "pending" forever and the deploy never fired.
Every branch is pinned here so that cannot recur silently.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# Loaded by path: deploy/ is operational tooling, deliberately not part of the
# installed package, but it still gets tested.
_spec = importlib.util.spec_from_file_location(
    "ci_state", Path(__file__).resolve().parents[1] / "deploy" / "ci_state.py"
)
assert _spec and _spec.loader
ci_state = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ci_state)


def run(name="lint + test", status="completed", conclusion="success"):
    return {"name": name, "status": status, "conclusion": conclusion}


def test_all_green_is_success():
    doc = {"check_runs": [run(), run(name="other")]}
    assert ci_state.classify(doc) == "success"


def test_skipped_and_neutral_count_as_green():
    doc = {
        "check_runs": [
            run(conclusion="success"),
            run(name="optional", conclusion="skipped"),
            run(name="advisory", conclusion="neutral"),
        ]
    }
    assert ci_state.classify(doc) == "success"


@pytest.mark.parametrize("status", ["queued", "in_progress"])
def test_unfinished_check_is_pending(status):
    doc = {"check_runs": [run(), run(name="slow", status=status, conclusion=None)]}
    assert ci_state.classify(doc) == "pending"


def test_no_checks_is_none_not_success():
    """An Actions-only repo with no runs yet must not read as green."""
    assert ci_state.classify({"check_runs": [], "total_count": 0}) == "none"


def test_missing_key_is_unreachable():
    """An error payload (rate limit, 404) must never be read as green."""
    assert ci_state.classify({"message": "API rate limit exceeded"}) == "unreachable"


@pytest.mark.parametrize(
    "conclusion", ["failure", "cancelled", "timed_out", "action_required", None]
)
def test_red_conclusions_block_the_deploy(conclusion):
    doc = {"check_runs": [run(conclusion=conclusion)]}
    verdict = ci_state.classify(doc)
    assert verdict.startswith("failed:")
    assert "lint + test" in verdict


def test_one_red_among_greens_still_blocks():
    doc = {
        "check_runs": [
            run(name="lint", conclusion="success"),
            run(name="test", conclusion="failure"),
        ]
    }
    verdict = ci_state.classify(doc)
    assert verdict == "failed:test=failure"


def test_verdict_never_returns_success_for_a_failed_lookup():
    """The single most important property: a broken lookup is not a green light."""
    for doc in ({}, {"check_runs": None}, {"message": "Not Found"}):
        assert ci_state.classify(doc) != "success"
