"""Step 11c: the time bar's future comes from the batch's upcoming planned steps."""
import datetime
import html

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from ..factories import BatchFactory, FermenterFactory, RecipeFactory, VesselFactory
from ..models import AgingTank, BatchStage, PlanStep, Vessel
from ..services import (
    copy_plan_to_batch,
    plan_progress,
    save_recipe_plan,
    transition_stage_event,
)

FERMENTATION, AGING, BOTTLING = BatchStage.STATE_FERMENTATION, BatchStage.STATE_AGING, BatchStage.STATE_BOTTLING
FERMENTER, AGING_TANK, BARREL, CURRENT = (
    Vessel.TYPE_FERMENTER, Vessel.TYPE_AGING_TANK, Vessel.TYPE_BARREL, PlanStep.VESSEL_CURRENT,
)
BOTTLES = PlanStep.VESSEL_BOTTLES
PLAN = [("pitch", "14 days", FERMENTER), ("racking", "30 days", AGING_TANK),
        ("coarse-filtering", None, AGING_TANK), ("sterile-filtering", "5 days", BOTTLES),
        ("complete-batch", None, CURRENT)]
DAY = 86400.0


def _stage(shortid):
    return BatchStage.objects.get(shortid=shortid)


def _ago(days):
    return timezone.now() - datetime.timedelta(days=days)


def _tank(name):
    vessel = VesselFactory(name=name, status=Vessel.STATUS_READY)
    AgingTank.objects.create(vessel=vessel)
    return vessel


def _planned_batch():
    recipe = RecipeFactory()
    save_recipe_plan(recipe, [{"stage": _stage(s), "planned_duration": d, "vessel_type": v, "notes": ""}
                              for s, d, v in PLAN])
    carboy = VesselFactory(name="Carboy 1", status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(recipe=recipe, fermenter=FermenterFactory(vessel=carboy), vessel=carboy)
    batch.startdate = _ago(200)
    batch.save()
    copy_plan_to_batch(batch)
    return batch


def _log(batch, shortid, days_ago, dst=None, packaging=""):
    return transition_stage_event(batch, _stage(shortid), timestamp=_ago(days_ago), dst_vessel=dst,
                                  packaging=packaging)


def _bar(batch):
    """The time bar the way the batch page builds it."""
    upcoming = [r.step for r in plan_progress(batch) if r.status == "upcoming"]
    return batch.timeline_bar(planned_steps=upcoming)


def _future(bar):
    return [(s.state, s.label, s.planned_vessel, s.planned, s.weight) for s in bar.segments if s.kind == "future"]


# ---------- Future segments from the plan ----------

@pytest.mark.django_db
def test_an_unpitched_batch_shows_every_planned_step_to_scale():
    bar = _bar(_planned_batch())

    assert _future(bar) == [
        (FERMENTATION, "Pitch", "Any Fermenter", True, 14 * DAY),
        (AGING, "Racking", "Any Aging Tank", True, 30 * DAY),
        (AGING, "Coarse Filtering", "Any Aging Tank", False, 0.0),     # no planned time: minimum width
        (BOTTLING, "Sterile Filtering", "Bottles", True, 5 * DAY),
    ]
    assert bar.planned_end    # Complete Batch is the end marker, not a segment


@pytest.mark.django_db
def test_only_the_steps_still_ahead_are_future():
    batch = _planned_batch()
    _log(batch, "pitch", 20)

    bar = _bar(batch)

    assert [s.kind for s in bar.segments] == ["current", "future", "future", "future"]
    assert [s.label for s in bar.segments if s.kind == "future"] == ["Racking", "Coarse Filtering",
                                                                      "Sterile Filtering"]


@pytest.mark.django_db
def test_skipped_steps_are_not_drawn_ahead():
    batch = _planned_batch()
    _log(batch, "pitch", 60)
    _log(batch, "racking", 40, dst=_tank("Tank A"))
    _log(batch, "sterile-filtering", 10, dst=_tank("Tank B"))   # Coarse Filtering skipped

    bar = _bar(batch)

    assert _future(bar) == []
    assert bar.planned_end


@pytest.mark.django_db
def test_a_planned_segment_carries_its_duration_and_no_planned_time_has_none():
    bar = _bar(_planned_batch())

    racking, coarse = [s for s in bar.segments if s.label in ("Racking", "Coarse Filtering")]
    assert racking.duration == datetime.timedelta(days=30)
    assert coarse.duration is None and coarse.from_plan


@pytest.mark.django_db
def test_a_band_totals_the_planned_time_it_knows():
    bands = _bar(_planned_batch()).bands

    aging = next(b for b in bands if b.state == AGING)
    assert aging.duration == datetime.timedelta(days=30)     # Racking 30 + Coarse (none)


@pytest.mark.django_db
def test_a_packaged_stay_shows_its_packaging():
    batch = _planned_batch()
    _log(batch, "pitch", 60)
    _log(batch, "racking", 40, dst=_tank("Tank A"))
    _log(batch, "sterile-filtering", 10, packaging=BOTTLES)

    current = _bar(batch).segments[-1]

    assert (current.kind, current.label, current.vessel, current.packaging) == \
        ("current", "Sterile Filtering", None, "Bottles")


@pytest.mark.django_db
def test_a_completed_batch_has_no_future_and_no_planned_end():
    batch = _planned_batch()
    _log(batch, "pitch", 60)
    _log(batch, "racking", 40, dst=_tank("Tank A"))
    _log(batch, "sterile-filtering", 10, packaging=BOTTLES)
    _log(batch, "complete-batch", 5)

    bar = _bar(batch)

    assert _future(bar) == [] and not bar.planned_end and bar.completed_at is not None


@pytest.mark.django_db
def test_a_batch_without_a_plan_keeps_the_placeholders():
    carboy = VesselFactory(status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(fermenter=FermenterFactory(vessel=carboy), vessel=carboy)

    bar = batch.timeline_bar()

    assert [s.label for s in bar.segments] == ["Pitch", "Racking", "Sterile Filtering"]
    assert not any(s.from_plan for s in bar.segments) and not bar.planned_end


# ---------- On the batch page ----------

@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


def _timebar_html(client, batch):
    page = html.unescape(client.get(reverse("batch", kwargs={"pk": batch.pk})).content.decode())
    start = page.index('<div class="cl-timebar"')
    return page[start:page.index("</ol>", start) + 800]


@pytest.mark.django_db
def test_batch_page_draws_the_planned_steps(client):
    batch = _planned_batch()
    _log(batch, "pitch", 20)

    bar = _timebar_html(client, batch)

    assert "~30 days planned" in bar and "Any Aging Tank" in bar
    assert "No planned time" in bar and "Bottles" in bar
    assert "Complete Batch" in bar


@pytest.mark.django_db
def test_batch_page_shows_a_packaged_stay_as_bottles(client):
    batch = _planned_batch()
    _log(batch, "pitch", 60)
    _log(batch, "racking", 40, dst=_tank("Tank A"))
    _log(batch, "sterile-filtering", 10, packaging=BOTTLES)

    bar = _timebar_html(client, batch)

    assert '<span class="cl-timebar-vessel">Bottles</span>' in bar
