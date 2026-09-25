"""
The batch page's Plan table is collapsed to one summary line (Aaron, 2026-09-25):
a closed <details class="cl-table-toggle"> whose <summary> is the current step -
or the last step that happened when none is Current (latest stage unplanned, or
batch completed) - and "Not started" before Pitch. Opening it shows the full table.
"""
import re

import pytest

from ..models import PlanStep
from .test_batch_plan_vs_actual import _batch, _log, _section, _tank, _text, client  # noqa: F401


def _toggle(section):
    match = re.search(r'<details class="cl-table-toggle cl-plan-toggle">\s*<summary>(.*?)</summary>(.*?)</details>',
                      section, re.DOTALL)
    assert match, "the plan table should be inside a closed cl-table-toggle"
    return _text(match.group(1)), match.group(2)


@pytest.mark.django_db
def test_before_pitch_the_summary_says_not_started_and_the_plan_is_inside(client):
    batch = _batch()

    summary, inside = _toggle(_section(client, batch))

    assert summary == "Not started"
    assert "<table" in inside and "Pitch" in inside


@pytest.mark.django_db
def test_after_pitch_the_summary_is_the_current_step(client):
    batch = _batch()
    _log(batch, "pitch", 0)
    _log(batch, "racking", 14, dst=_tank("Tank A"))

    summary, inside = _toggle(_section(client, batch))

    assert summary.startswith("Step 2 · Racking · Current · Tank A · ")
    assert "so far" in summary
    assert 'class="cl-ledger cl-plan-progress"' in inside


@pytest.mark.django_db
def test_an_unplanned_latest_stage_is_the_summary(client):
    batch = _batch()
    _log(batch, "pitch", 0)
    _log(batch, "racking", 14, dst=_tank("Tank A"))
    _log(batch, "racking", 20, dst=_tank("Tank B"))   # the plan has one Racking

    summary, _inside = _toggle(_section(client, batch))

    assert summary.startswith("Racking · Unplanned · Tank B · ")


@pytest.mark.django_db
def test_a_completed_batch_shows_its_last_step(client):
    batch = _batch()
    _log(batch, "pitch", 0)
    _log(batch, "racking", 14, dst=_tank("Tank A"))
    _log(batch, "sterile-filtering", 50, packaging=PlanStep.VESSEL_BOTTLES)
    _log(batch, "complete-batch", 60)

    summary, _inside = _toggle(_section(client, batch))

    assert summary.startswith("Step 6 · Complete Batch · Done")
    assert "—" not in summary
