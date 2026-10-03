"""
PROGRESS BAR build stages 1-2 (TODO.txt): services.schedule_progress() - the batch page's
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
from ..models import (
    ActivityLog,
    AgingTank,
    BatchNote,
    BatchNoteType,
    BatchStage,
    BatchTest,
    BatchTestType,
    PlanStep,
    Vessel,
)
from ..services import (
    ProgressStage,
    batch_schedule_progress,
    copy_plan_to_batch,
    plan_progress,
    progress_stages,
    save_recipe_plan,
    schedule_progress,
    transfer_batch,
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


def _progress(batch, flags=None):
    completed = next((e for e in batch.stage_events.select_related('stage')
                      if e.stage and e.stage.to_state == BatchStage.STATE_COMPLETED), None)
    return batch_schedule_progress(batch.vessel_durations(), list(batch.plan_steps.select_related('stage')),
                                   completed_at=completed.timestamp if completed else None,
                                   plan_rows=plan_progress(batch), full_aging=batch.full_aging(),
                                   notes=list(batch.notes.select_related('notetype')),
                                   tests=list(batch.tests.select_related('type')), flags=flags or [], now=NOW)


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
    rows, full_aging = plan_progress(batch), batch.full_aging()
    _note(batch, 20, "Clearing")
    notes, tests = list(batch.notes.select_related('notetype')), list(batch.tests.select_related('type'))

    with django_assert_num_queries(0):
        batch_schedule_progress(stays, steps, completed_at=None, plan_rows=rows, full_aging=full_aging,
                                notes=notes, tests=tests, flags=[], now=NOW)


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


# ---------- Build stage 2: vessel stays and steps on the bar ----------

def _stays(progress):
    return [(s.kind, s.label) for s in progress.stays]


def _steps(progress):
    return [(s.name, s.status) for s in progress.steps]


@pytest.mark.django_db
def test_vessel_stays_sit_inside_their_stage_and_the_plan_draws_whats_ahead():
    batch = _batch()
    _log(batch, "pitch", 67)
    _log(batch, "racking", 41, dst=_tank("Tank A"))

    progress = _progress(batch)
    ferm, aging, bottling = progress.segments
    carboy, tank, bottles = progress.stays

    assert _stays(progress) == [("past", "Carboy 1"), ("current", "Tank A"), ("planned", "Bottles")]
    assert (carboy.left, carboy.width) == (pytest.approx(0), pytest.approx(ferm.width))
    assert tank.left == pytest.approx(aging.left)
    assert tank.width == pytest.approx(aging.width * 41 / 120)
    assert tank.tail == pytest.approx(aging.width * 79 / 120)       # dashed to Racking's planned 120 days
    assert tank.vessel.name == "Tank A" and tank.step == "Racking"
    assert (bottles.left, bottles.width) == (pytest.approx(bottling.left), pytest.approx(bottling.width))
    assert bottles.days == 14


@pytest.mark.django_db
def test_steps_are_markers_done_where_they_happened_and_ahead_where_planned():
    batch = _batch()
    _log(batch, "pitch", 67)
    _log(batch, "racking", 41, dst=_tank("Tank A"))

    progress = _progress(batch)
    aging, bottling = progress.segments[1:]
    pitch, racking, sterile, complete = progress.steps

    assert _steps(progress) == [("Pitch", "done"), ("Racking", "done"),
                                ("Sterile Filtering", "ahead"), ("Complete Batch", "ahead")]
    assert pitch.at == pytest.approx(0) and pitch.when == _ago(67)
    assert racking.at == pytest.approx(aging.left) and racking.when == _ago(41)
    assert sterile.at == pytest.approx(bottling.left) and sterile.when is None
    assert complete.at == pytest.approx(100)


@pytest.mark.django_db
def test_a_second_racking_starts_a_new_stay_where_it_happened():
    batch = _batch()
    _log(batch, "pitch", 100)
    _log(batch, "racking", 70, dst=_tank("Tank A"))
    _log(batch, "racking", 40, dst=_tank("Tank B"))

    progress = _progress(batch)
    aging = progress.segments[1]
    tank_a, tank_b = progress.stays[1:3]

    assert _stays(progress)[:3] == [("past", "Carboy 1"), ("past", "Tank A"), ("current", "Tank B")]
    assert tank_a.width == pytest.approx(aging.width * 30 / 120)
    assert tank_b.left == pytest.approx(aging.left + aging.width * 30 / 120)
    assert _steps(progress)[:3] == [("Pitch", "done"), ("Racking", "done"), ("Racking", "done")]


@pytest.mark.django_db
def test_an_ad_hoc_transfer_is_a_step_and_a_new_stay():
    batch = _batch()
    _log(batch, "pitch", 67)
    _log(batch, "racking", 41, dst=_tank("Tank A"))
    transfer_batch(batch, _tank("Tank B"), reason="Tank A leaking", timestamp=_ago(10))

    progress = _progress(batch)

    assert _stays(progress)[1:3] == [("past", "Tank A"), ("current", "Tank B")]
    assert ("Transfer", "done") in _steps(progress)


@pytest.mark.django_db
def test_a_completed_batch_has_only_what_happened():
    batch = _batch()
    _log(batch, "pitch", 168)
    _log(batch, "racking", 140, dst=_tank("Tank A"))
    _log(batch, "sterile-filtering", 9, packaging=BOTTLES)
    _log(batch, "complete-batch", 0)

    progress = _progress(batch)

    assert _stays(progress) == [("past", "Carboy 1"), ("past", "Tank A"), ("past", "Bottles")]
    assert progress.stays[2].vessel is None
    assert _steps(progress) == [("Pitch", "done"), ("Racking", "done"), ("Sterile Filtering", "done"),
                                ("Complete Batch", "done")]
    assert progress.steps[-1].at == pytest.approx(100)


@pytest.mark.django_db
def test_without_a_plan_only_what_happened_is_drawn():
    batch = _batch(plan=None)
    _log(batch, "pitch", 87)
    _log(batch, "racking", 49, dst=_tank("Tank A"))

    progress = _progress(batch)

    assert _stays(progress) == [("past", "Carboy 1"), ("current", "Tank A")]
    assert progress.stays[1].tail == 0
    assert _steps(progress) == [("Pitch", "done"), ("Racking", "done")]


@pytest.mark.django_db
def test_an_unpitched_batch_shows_the_planned_stays_and_steps():
    progress = _progress(_batch())

    assert _stays(progress) == [("planned", "Any Fermenter"), ("planned", "Any Aging Tank"), ("planned", "Bottles")]
    assert _steps(progress) == [("Pitch", "ahead"), ("Racking", "ahead"), ("Sterile Filtering", "ahead"),
                                ("Complete Batch", "ahead")]


@pytest.mark.django_db
def test_full_aging_is_noted_on_the_aging_stage():
    batch = _batch()
    _log(batch, "pitch", 67)
    _log(batch, "racking", 41, dst=_tank("Tank A"))

    aging = _progress(batch).segments[1]

    assert aging.stage.note == "Full aging 41 d so far"


def test_step_labels_too_close_together_go_on_the_second_row():
    stages = [ProgressStage("Fermentation", "done", 30, 30), ProgressStage("Aging", "current", 300, 100)]
    progress = schedule_progress(stages, started_at=_ago(130))
    from ..services import _step_label_rows

    assert _step_label_rows([0.0, 3.0, 40.0, 42.0, 44.0]) == [0, 1, 0, 1, 0]
    assert progress.steps == []          # stage list alone has no stays or steps


@pytest.mark.django_db
def test_after_an_unplanned_racking_the_current_stay_runs_on_through_the_stages_planned_time():
    batch = _batch()
    _log(batch, "pitch", 100)
    _log(batch, "racking", 70, dst=_tank("Tank A"))
    _log(batch, "racking", 40, dst=_tank("Tank B"))     # not in the plan

    progress = _progress(batch)
    aging = progress.segments[1]
    tank_b = progress.stays[2]

    assert tank_b.kind == "current" and tank_b.label == "Tank B"
    assert tank_b.right + tank_b.tail == pytest.approx(aging.left + aging.width)   # to Aging's planned 120 days


# Ported from test_timeline_plan.py when the old time bar was retired (build stage 2c).
WITH_COARSE = [("pitch", "14 days", FERMENTER), ("racking", "30 days", AGING_TANK),
               ("coarse-filtering", None, AGING_TANK), ("sterile-filtering", "5 days", BOTTLES),
               ("complete-batch", None, CURRENT)]


@pytest.mark.django_db
def test_a_planned_step_with_no_planned_time_is_a_marker_only():
    progress = _progress(_batch(plan=WITH_COARSE))

    assert ("Coarse Filtering", "ahead") in _steps(progress)
    assert "Coarse Filtering" not in [s.step for s in progress.stays]


@pytest.mark.django_db
def test_skipped_steps_are_not_drawn_ahead():
    batch = _batch(plan=WITH_COARSE)
    _log(batch, "pitch", 60)
    _log(batch, "racking", 40, dst=_tank("Tank A"))
    _log(batch, "sterile-filtering", 10, dst=_tank("Tank B"))     # Coarse Filtering skipped

    progress = _progress(batch)

    assert "Coarse Filtering" not in [s.name for s in progress.steps]
    assert [s for s in progress.stays if s.kind == "planned"] == []
    assert _steps(progress)[-1] == ("Complete Batch", "ahead")


@pytest.mark.django_db
def test_a_packaged_current_stay_shows_its_packaging():
    batch = _batch(plan=WITH_COARSE)
    _log(batch, "pitch", 60)
    _log(batch, "racking", 40, dst=_tank("Tank A"))
    _log(batch, "sterile-filtering", 10, packaging=BOTTLES)

    current = _progress(batch).stays[-1]

    assert (current.kind, current.label, current.vessel, current.step) == \
        ("current", "Bottles", None, "Sterile Filtering")



# ---------- Build stage 3: notes, readings and system flags on the bar ----------

def _note(batch, days_ago, text, kind="General Note"):
    return BatchNote.objects.create(batch=batch, date=_ago(days_ago), text=text,
                                    notetype=BatchNoteType.objects.get(name=kind))


def _reading(batch, days_ago, shortid, value):
    return BatchTest.objects.create(batch=batch, datetime=_ago(days_ago), value=value,
                                    type=BatchTestType.objects.get(shortid=shortid))


def _marks(progress, kind):
    return [m for m in progress.marks if m.kind == kind]


@pytest.mark.django_db
def test_a_note_is_pinned_at_its_date():
    batch = _batch()
    _log(batch, "pitch", 67)
    _log(batch, "racking", 41, dst=_tank("Tank A"))
    _note(batch, 31, "Clearing nicely", kind="Fermentation Note")      # 10 days into Aging

    progress = _progress(batch)
    aging = progress.segments[1]
    (note,) = _marks(progress, "note")

    assert (note.label, note.text, note.when) == ("Fermentation Note", "Clearing nicely", _ago(31))
    assert note.at == pytest.approx(aging.left + aging.width * 10 / 120)


@pytest.mark.django_db
def test_readings_are_dots_labelled_with_their_display_value():
    batch = _batch()
    _log(batch, "pitch", 67)
    _reading(batch, 60, "specific-gravity", "1.02 sg")

    progress = _progress(batch)
    ferm = progress.segments[0]
    reading = next(m for m in _marks(progress, "reading") if m.when == _ago(60))

    assert reading.label.endswith("1.020")                              # SG always 3 decimals
    assert reading.at == pytest.approx(ferm.width * 7 / 67)            # Fermentation is current: 67 d of 67


@pytest.mark.django_db
def test_a_system_fault_flag_is_pinned_at_the_reading_that_tripped_it():
    batch = _batch()
    _log(batch, "pitch", 67)
    _log(batch, "racking", 41, dst=_tank("Tank A"))
    low = _reading(batch, 21, "so2", "4 ppm")
    flag = {"batch": batch, "test": low, "severity": "warning", "label": "Free SO₂ low", "message": "Too low"}

    progress = _progress(batch, flags=[flag])
    aging = progress.segments[1]
    (mark,) = _marks(progress, "flag")

    assert (mark.label, mark.text, mark.when) == ("Free SO₂ low", "Too low", _ago(21))
    assert mark.at == pytest.approx(aging.left + aging.width * 20 / 120)


@pytest.mark.django_db
def test_an_overrun_is_flagged_where_the_stage_passed_its_plan():
    batch = _batch()
    _log(batch, "pitch", 162)
    _log(batch, "racking", 132, dst=_tank("Tank A"))                   # Aging 132 d of 120

    progress = _progress(batch)
    aging = progress.segments[1]
    (mark,) = _marks(progress, "flag")

    assert mark.label == "Aging: 12 days over plan"
    assert mark.when == _ago(12)                                        # Aging started 132 d ago + 120 planned
    assert mark.at == pytest.approx(aging.left + aging.width * 120 / 132)


@pytest.mark.django_db
def test_notes_before_pitch_and_after_completion_sit_at_the_ends():
    batch = _batch()
    _note(batch, 200, "Ordered honey")
    _log(batch, "pitch", 168)
    _log(batch, "racking", 140, dst=_tank("Tank A"))
    _log(batch, "sterile-filtering", 9, packaging=BOTTLES)
    _log(batch, "complete-batch", 5)
    _note(batch, 1, "Tasted a bottle")

    notes = {m.text: m.at for m in _marks(_progress(batch), "note")}

    assert notes == {"Ordered honey": pytest.approx(0), "Tasted a bottle": pytest.approx(100)}


@pytest.mark.django_db
def test_an_unpitched_batch_has_no_marks():
    batch = _batch()
    _note(batch, 3, "Honey arrived")

    assert _progress(batch).marks == []
