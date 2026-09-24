"""Step 11b: logged stages link to their planned step; plan_progress() compares plan vs actual."""
import datetime

import pytest
from django.utils import timezone

from ..factories import BatchFactory, FermenterFactory, RecipeFactory, VesselFactory
from ..models import AgingTank, BatchStage, PlanStep, Vessel
from ..services import (
    copy_plan_to_batch,
    plan_progress,
    save_recipe_plan,
    transfer_batch,
    transition_stage_event,
)

FERMENTER, AGING_TANK, BARREL, CURRENT = (
    Vessel.TYPE_FERMENTER, Vessel.TYPE_AGING_TANK, Vessel.TYPE_BARREL, PlanStep.VESSEL_CURRENT,
)
BOTTLES = PlanStep.VESSEL_BOTTLES
PLAN = [("pitch", "14 days", FERMENTER), ("racking", "30 days", AGING_TANK),
        ("coarse-filtering", None, AGING_TANK), ("fine-filtering", "7 days", BARREL),
        ("sterile-filtering", "5 days", BOTTLES), ("complete-batch", None, CURRENT)]


def _stage(shortid):
    return BatchStage.objects.get(shortid=shortid)


def _at(day):
    """Day `day` after Pitch; Pitch was 100 days ago."""
    return timezone.now() - datetime.timedelta(days=100) + datetime.timedelta(days=day)


def _tank(name):
    vessel = VesselFactory(name=name, status=Vessel.STATUS_READY)
    AgingTank.objects.create(vessel=vessel)
    return vessel


def _planned_batch(plan=PLAN):
    recipe = RecipeFactory()
    save_recipe_plan(recipe, [{"stage": _stage(s), "planned_duration": d, "vessel_type": v, "notes": ""}
                              for s, d, v in plan])
    carboy = VesselFactory(name="Carboy 1", status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(recipe=recipe, fermenter=FermenterFactory(vessel=carboy), vessel=carboy)
    batch.startdate = _at(-1)
    batch.save()
    copy_plan_to_batch(batch)
    return batch


def _log(batch, shortid, day, dst=None, packaging=""):
    return transition_stage_event(batch, _stage(shortid), timestamp=_at(day), dst_vessel=dst, packaging=packaging)


def _row_summary(rows):
    return [(r.stage.shortid, r.status) for r in rows]


# ---------- Linking each logged stage to its planned step ----------

@pytest.mark.django_db
def test_each_logged_stage_links_to_its_planned_step_in_order():
    batch = _planned_batch()
    steps = list(batch.plan_steps.all())

    pitch = _log(batch, "pitch", 0)
    racking = _log(batch, "racking", 17, dst=_tank("Tank A"))

    assert pitch.plan_step == steps[0] and racking.plan_step == steps[1]


@pytest.mark.django_db
def test_an_extra_racking_is_unplanned():
    batch = _planned_batch()
    _log(batch, "pitch", 0)
    _log(batch, "racking", 17, dst=_tank("Tank A"))

    extra = _log(batch, "racking", 40, dst=_tank("Tank B"))

    assert extra.plan_step is None


@pytest.mark.django_db
def test_logging_a_later_planned_stage_skips_the_ones_in_between():
    batch = _planned_batch()
    steps = list(batch.plan_steps.all())
    _log(batch, "pitch", 0)
    _log(batch, "racking", 17, dst=_tank("Tank A"))

    fine = _log(batch, "fine-filtering", 50, dst=_tank("Tank B"))   # Coarse Filtering never happened

    assert fine.plan_step == steps[3]


@pytest.mark.django_db
def test_a_transfer_does_not_use_up_a_planned_step():
    batch = _planned_batch()
    _log(batch, "pitch", 0)
    transfer = transfer_batch(batch, _tank("Tank A"), reason="Pump failure", timestamp=_at(5))
    racking = _log(batch, "racking", 17, dst=_tank("Tank B"))

    assert transfer.plan_step is None
    assert racking.plan_step == batch.plan_steps.get(sort_order=2)


@pytest.mark.django_db
def test_a_batch_without_a_plan_logs_stages_unlinked():
    carboy = VesselFactory(status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(fermenter=FermenterFactory(vessel=carboy), vessel=carboy)
    batch.startdate = _at(-1)
    batch.save()

    assert _log(batch, "pitch", 0).plan_step is None
    assert plan_progress(batch) == []


# ---------- plan_progress(): plan vs actual ----------

@pytest.mark.django_db
def test_before_pitch_every_step_is_upcoming_with_its_planned_day():
    batch = _planned_batch()

    rows = plan_progress(batch)

    assert {r.status for r in rows} == {"upcoming"}
    # Planned start day = the planned time of the steps before it (untimed steps add nothing).
    assert [r.planned_day for r in rows] == [0, 14, 44, 44, 51, 56]
    assert all(r.actual_day is None for r in rows)


@pytest.mark.django_db
def test_plan_vs_actual_start_day_and_time_spent():
    batch = _planned_batch()
    _log(batch, "pitch", 0)
    _log(batch, "racking", 17, dst=_tank("Tank A"))
    _log(batch, "coarse-filtering", 50, dst=_tank("Tank B"))

    pitch, racking, coarse, fine = plan_progress(batch)[:4]

    assert (pitch.status, pitch.actual_day, pitch.planned_days, pitch.actual_days, pitch.difference_days) == \
        ("done", 0, 14, 17, 3)
    assert (racking.status, racking.planned_day, racking.actual_day, racking.actual_days,
            racking.difference_days) == ("done", 14, 17, 33, 3)
    assert coarse.status == "current" and coarse.actual_day == 50
    assert coarse.actual_days == pytest.approx(50, abs=0.01)    # still going: measured up to now
    assert coarse.difference_days is None                         # no planned time to compare with
    assert fine.status == "upcoming"


@pytest.mark.django_db
def test_rows_show_planned_vessel_type_and_actual_vessel_or_packaging():
    batch = _planned_batch()
    _log(batch, "pitch", 0)
    _log(batch, "racking", 17, dst=_tank("Tank A"))
    _log(batch, "sterile-filtering", 60, packaging=BOTTLES)

    rows = {r.stage.shortid: r for r in plan_progress(batch)}

    assert (rows["pitch"].planned_vessel, rows["pitch"].actual_vessel) == ("Fermenter", "Carboy 1")
    assert (rows["racking"].planned_vessel, rows["racking"].actual_vessel) == ("Aging Tank", "Tank A")
    assert (rows["sterile-filtering"].planned_vessel, rows["sterile-filtering"].actual_vessel) == ("Bottles", "Bottles")


@pytest.mark.django_db
def test_skipped_and_unplanned_steps_appear_in_order():
    batch = _planned_batch()
    _log(batch, "pitch", 0)
    _log(batch, "racking", 17, dst=_tank("Tank A"))
    _log(batch, "racking", 40, dst=_tank("Tank B"))             # extra racking
    _log(batch, "fine-filtering", 50, dst=_tank("Tank C"))      # Coarse Filtering skipped

    assert _row_summary(plan_progress(batch)) == [
        ("pitch", "done"), ("racking", "done"), ("racking", "unplanned"),
        ("coarse-filtering", "skipped"), ("fine-filtering", "current"),
        ("sterile-filtering", "upcoming"), ("complete-batch", "upcoming"),
    ]


@pytest.mark.django_db
def test_a_completed_batch_is_all_done_and_complete_batch_has_no_time():
    batch = _planned_batch()
    _log(batch, "pitch", 0)
    _log(batch, "racking", 14, dst=_tank("Tank A"))
    _log(batch, "coarse-filtering", 44, dst=_tank("Tank B"))
    _log(batch, "fine-filtering", 44, dst=_tank("Tank C"))
    _log(batch, "sterile-filtering", 51, packaging=BOTTLES)
    _log(batch, "complete-batch", 56)

    rows = plan_progress(batch)

    assert {r.status for r in rows} == {"done"}
    assert rows[-1].actual_days is None and rows[-1].actual_day == 56
    assert rows[4].actual_days == 5 and rows[4].difference_days == 0
