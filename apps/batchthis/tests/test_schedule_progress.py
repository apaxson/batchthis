"""
PROGRESS BAR build stage 1 (TODO.txt): services.schedule_progress() - the batch page's
% of plan, expected total, remaining time and forecast, from the stage list
services.progress_stages() builds out of the vessel stays and the batch plan.

expected total = actual time of completed stages
               + max(planned, elapsed) of the current stage
               + planned time of the stages still ahead
"""
import datetime

import pytest
from django.utils import timezone

from ..factories import BatchFactory, FermenterFactory, RecipeFactory, VesselFactory
from ..models import ActivityLog, AgingTank, BatchStage, PlanStep, Vessel
from ..services import (
    ProgressStage,
    batch_schedule_progress,
    copy_plan_to_batch,
    progress_stages,
    save_recipe_plan,
    schedule_progress,
    transition_stage_event,
)

FERMENTER, AGING_TANK, BOTTLES, CURRENT = (
    Vessel.TYPE_FERMENTER, Vessel.TYPE_AGING_TANK, PlanStep.VESSEL_BOTTLES, PlanStep.VESSEL_CURRENT,
)
# Fermentation 30 d, Aging 120 d, Bottling 14 d = 164 d planned.
PLAN = [("pitch", "30 days", FERMENTER), ("racking", "120 days", AGING_TANK),
        ("sterile-filtering", "14 days", BOTTLES), ("complete-batch", None, CURRENT)]

NOW = timezone.now()


def _stage(shortid):
    return BatchStage.objects.get(shortid=shortid)


def _ago(days):
    return NOW - datetime.timedelta(days=days)


def _tank(name):
    vessel = VesselFactory(name=name, status=Vessel.STATUS_READY)
    AgingTank.objects.create(vessel=vessel)
    return vessel


def _batch(plan=PLAN, started_days_ago=400):
    carboy = VesselFactory(name="Carboy 1", status=Vessel.STATUS_ACTIVE)
    recipe = RecipeFactory()
    if plan:
        save_recipe_plan(recipe, [{"stage": _stage(s), "planned_duration": d, "vessel_type": v, "notes": ""}
                                  for s, d, v in plan])
    batch = BatchFactory(recipe=recipe, fermenter=FermenterFactory(vessel=carboy), vessel=carboy)
    batch.startdate = _ago(started_days_ago)
    batch.save()
    if plan:
        copy_plan_to_batch(batch)
    return batch


def _log(batch, shortid, days_ago, dst=None, packaging=""):
    return transition_stage_event(batch, _stage(shortid), timestamp=_ago(days_ago), dst_vessel=dst,
                                  packaging=packaging)


def _progress(batch):
    completed = next((e for e in batch.stage_events.select_related('stage')
                      if e.stage and e.stage.to_state == BatchStage.STATE_COMPLETED), None)
    return batch_schedule_progress(batch.vessel_durations(), list(batch.plan_steps.select_related('stage')),
                                   completed_at=completed.timestamp if completed else None, now=NOW)


def _summary(progress):
    return [(seg.stage.name, seg.stage.status, round(seg.expected_days)) for seg in progress.segments]


# ---------- The expected-total formula on a real batch ----------

@pytest.mark.django_db
def test_in_progress_batch_uses_actual_then_current_then_planned_time():
    """Pitched 67 days ago, racked on day 26: Fermentation 26 (actual) + Aging 120 (plan) + Bottling 14."""
    batch = _batch()
    _log(batch, "pitch", 67)
    _log(batch, "racking", 41, dst=_tank("Tank A"))

    progress = _progress(batch)

    assert _summary(progress) == [("Fermentation", "done", 26), ("Aging", "current", 120), ("Bottling", "ahead", 14)]
    assert progress.has_plan and progress.pitched and not progress.completed
    assert progress.elapsed_days == 67
    assert progress.expected_total_days == 160
    assert progress.remaining_days == 93
    assert progress.percent == 41                       # 67 / 160 = 41.9 -> floor, never rounds up to done
    assert progress.forecast_end == _ago(67) + datetime.timedelta(days=160)


@pytest.mark.django_db
def test_a_stage_finished_early_shrinks_the_total_and_one_finished_late_grows_it():
    early = _batch()
    _log(early, "pitch", 50)
    _log(early, "racking", 26, dst=_tank("Tank A"))     # Fermentation 24 d, planned 30

    late = _batch()
    _log(late, "pitch", 50)
    _log(late, "racking", 10, dst=_tank("Tank B"))      # Fermentation 40 d, planned 30

    assert _progress(early).expected_total_days == 164 - 6
    assert _progress(late).expected_total_days == 164 + 10


@pytest.mark.django_db
def test_computing_progress_changes_nothing_stored():
    """A calculation, not a plan change: planned times stay and nothing is logged."""
    batch = _batch()
    _log(batch, "pitch", 200)
    _log(batch, "racking", 170, dst=_tank("Tank A"))
    planned = [(s.stage.shortid, s.planned_days) for s in batch.plan_steps.select_related('stage')]
    logged = ActivityLog.objects.count()

    _progress(batch)

    assert [(s.stage.shortid, s.planned_days) for s in batch.plan_steps.select_related('stage')] == planned
    assert ActivityLog.objects.count() == logged


@pytest.mark.django_db
def test_progress_from_loaded_stays_and_steps_needs_no_queries(django_assert_num_queries):
    batch = _batch()
    _log(batch, "pitch", 67)
    _log(batch, "racking", 41, dst=_tank("Tank A"))
    stays, steps = batch.vessel_durations(), list(batch.plan_steps.select_related('stage'))

    with django_assert_num_queries(0):
        batch_schedule_progress(stays, steps, completed_at=None, now=NOW)


# ---------- A current stage past its plan ----------

@pytest.mark.django_db
def test_a_current_stage_past_its_plan_stretches_the_total_by_exactly_the_overrun():
    batch = _batch()
    _log(batch, "pitch", 162)
    _log(batch, "racking", 132, dst=_tank("Tank A"))    # Fermentation 30 d (on plan); Aging 132 d of 120

    progress = _progress(batch)

    assert _summary(progress) == [("Fermentation", "done", 30), ("Aging", "current", 132), ("Bottling", "ahead", 14)]
    assert progress.expected_total_days == 30 + 132 + 14
    assert progress.remaining_days == 14                 # only the stage not started
    assert progress.percent < 100
    aging = progress.segments[1]
    assert aging.overrun_days == 12
    assert progress.overruns == [("Aging", 12)]


def test_overrun_in_the_last_stage_stays_below_100_until_completed():
    stages = [ProgressStage("Fermentation", "done", 30, 30), ProgressStage("Aging", "current", 120, 200)]
    progress = schedule_progress(stages, started_at=_ago(230))

    assert progress.expected_total_days == 230 and progress.remaining_days == 0
    assert progress.percent == 99


def test_within_plan_the_current_stage_is_filled_to_elapsed_with_no_overrun():
    stages = [ProgressStage("Fermentation", "done", 30, 30), ProgressStage("Aging", "current", 120, 30),
              ProgressStage("Bottling", "ahead", 30, None)]
    aging = schedule_progress(stages, started_at=_ago(60)).segments[1]

    assert aging.overrun_days == 0 and aging.overrun == 0
    assert aging.fill == pytest.approx(25)               # 30 of 120 days


# ---------- Bar geometry ----------

def test_segment_widths_fill_the_bar_and_a_short_stage_keeps_a_minimum_width():
    stages = [ProgressStage("Fermentation", "done", 1, 1), ProgressStage("Aging", "current", 300, 10),
              ProgressStage("Bottling", "ahead", 2, None)]
    segments = schedule_progress(stages, started_at=_ago(11)).segments

    assert sum(s.width for s in segments) == pytest.approx(100)
    assert all(s.width >= 6 for s in segments)
    assert segments[1].left == pytest.approx(segments[0].width)


def test_the_today_marker_lands_inside_the_current_stage():
    stages = [ProgressStage("Fermentation", "done", 30, 26), ProgressStage("Aging", "current", 120, 41),
              ProgressStage("Bottling", "ahead", 14, None)]
    progress = schedule_progress(stages, started_at=_ago(67))
    aging = progress.segments[1]

    assert aging.left < progress.marker < aging.left + aging.width
    assert progress.marker == pytest.approx(aging.left + aging.width * 41 / 120)


# ---------- Not pitched, completed, no plan ----------

@pytest.mark.django_db
def test_an_unpitched_batch_is_at_zero_with_the_whole_plan_ahead():
    progress = _progress(_batch())

    assert not progress.pitched
    assert progress.percent == 0 and progress.elapsed_days == 0
    assert progress.expected_total_days == 164 and progress.remaining_days == 164
    assert progress.marker is None and progress.forecast_end is None
    assert [s.stage.status for s in progress.segments] == ["ahead", "ahead", "ahead"]


@pytest.mark.django_db
def test_a_completed_batch_is_at_100_with_actual_times():
    batch = _batch()
    _log(batch, "pitch", 168)
    _log(batch, "racking", 140, dst=_tank("Tank A"))
    _log(batch, "sterile-filtering", 9, packaging=BOTTLES)
    done = _log(batch, "complete-batch", 0)

    progress = _progress(batch)

    assert _summary(progress) == [("Fermentation", "done", 28), ("Aging", "done", 131), ("Bottling", "done", 9)]
    assert progress.completed and progress.percent == 100
    assert progress.remaining_days == 0 and progress.marker is None
    assert progress.completed_at == done.timestamp


@pytest.mark.django_db
def test_a_batch_without_a_plan_shows_elapsed_time_only():
    batch = _batch(plan=None)
    _log(batch, "pitch", 87)
    _log(batch, "racking", 49, dst=_tank("Tank A"))

    progress = _progress(batch)

    assert not progress.has_plan
    assert _summary(progress) == [("Fermentation", "done", 38), ("Aging", "current", 49)]
    assert progress.elapsed_days == 87
    assert progress.percent is None and progress.remaining_days is None
    assert progress.expected_total_days is None and progress.forecast_end is None


@pytest.mark.django_db
def test_a_plan_with_no_timed_steps_counts_as_no_plan():
    batch = _batch(plan=[("pitch", None, FERMENTER), ("racking", None, AGING_TANK)])
    _log(batch, "pitch", 20)

    progress = _progress(batch)

    assert not progress.has_plan and progress.percent is None


@pytest.mark.django_db
def test_an_unpitched_batch_without_a_plan_has_nothing_to_draw():
    progress = _progress(_batch(plan=None))

    assert progress.segments == [] and progress.percent is None


# ---------- The stage list (the part dynamic workflows phase 5 replaces) ----------

@pytest.mark.django_db
def test_progress_stages_add_up_every_stay_in_a_stage():
    """Two rackings in Aging are one Aging stage."""
    batch = _batch()
    _log(batch, "pitch", 100)
    _log(batch, "racking", 70, dst=_tank("Tank A"))
    _log(batch, "racking", 30, dst=_tank("Tank B"))

    stages = progress_stages(batch.vessel_durations(), list(batch.plan_steps.select_related('stage')), now=NOW)

    assert [(s.name, s.status, s.planned_days, round(s.actual_days or 0)) for s in stages] == [
        ("Fermentation", "done", 30, 30), ("Aging", "current", 120, 70), ("Bottling", "ahead", 14, 0)]
