"""
UI item 2 (Aaron, 2026-10-07): a "what's next" line on the batch page - the next plan
step (when it's expected, or how overdue) and the next timed addition from the batch
recipe. Additions aren't tracked as done yet (SCHEDULED ADDITIONS), so only one still
ahead is shown.
"""
import datetime
import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from pint import Quantity

from ..factories import (
    AdjunctFactory,
    AdjunctUsageFactory,
    BatchFactory,
    FermenterFactory,
    RecipeFactory,
    VesselFactory,
)
from ..models import AgingTank, BatchStage, PlanStep, RecipeAdjunct, Vessel
from ..services import (
    batch_next_steps,
    copy_plan_to_batch,
    copy_recipe_ingredients_to_batch,
    plan_progress,
    save_recipe_plan,
    transition_stage_event,
)

NOW = timezone.now()
PLAN = [("pitch", "30 days", Vessel.TYPE_FERMENTER), ("racking", "120 days", Vessel.TYPE_AGING_TANK),
        ("sterile-filtering", "14 days", PlanStep.VESSEL_BOTTLES), ("complete-batch", None, PlanStep.VESSEL_CURRENT)]


def _ago(days=0, hours=0):
    return NOW - datetime.timedelta(days=days, hours=hours)


def _stage(shortid):
    return BatchStage.objects.get(shortid=shortid)


def _tank(name):
    vessel = VesselFactory(name=name, status=Vessel.STATUS_READY)
    AgingTank.objects.create(vessel=vessel)
    return vessel


def _batch(plan=PLAN, additions=((24, 5), (48, 5))):
    recipe = RecipeFactory(batchSize=Quantity(6, "gallons"))
    if plan:
        save_recipe_plan(recipe, [{"stage": _stage(s), "planned_duration": d, "vessel_type": v, "notes": ""}
                                  for s, d, v in plan])
    use = AdjunctUsageFactory(name="Primary")
    fermaid = AdjunctFactory(name="Fermaid O")
    for hours, grams in additions:
        line = RecipeAdjunct.objects.create(adjunct=fermaid, intended_use=use, amount=Quantity(grams, "gram"),
                                            time_to_add=Quantity(hours, "hour"))
        line.recipe.add(recipe)
    carboy = VesselFactory(name="Carboy", status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(recipe=recipe, size=Quantity("6 gallons"), fermenter=FermenterFactory(vessel=carboy),
                         vessel=carboy)
    batch.startdate = _ago(200)
    batch.save()
    if plan:
        copy_plan_to_batch(batch)
    copy_recipe_ingredients_to_batch(batch)
    return batch


def _log(batch, shortid, at, dst=None):
    transition_stage_event(batch, _stage(shortid), timestamp=at, dst_vessel=dst)


def _next(batch):
    events = list(batch.stage_events.select_related('stage'))
    pitched = next((e.timestamp for e in events if e.stage and e.stage.shortid == "pitch"), None)
    completed = any(e.stage and e.stage.to_state == BatchStage.STATE_COMPLETED for e in events)
    return batch_next_steps(plan_progress(batch), list(batch.ingredients.select_related('adjunct')),
                            pitched_at=pitched, completed=completed, now=NOW)


# ---------- The next plan step ----------

@pytest.mark.django_db
def test_before_pitch_the_next_step_is_pitch_and_additions_are_relative():
    nxt = _next(_batch())

    assert (nxt.step.name, nxt.step.due, nxt.step.when) == ("Pitch", None, "")
    assert (nxt.addition.name, nxt.addition.amount, nxt.addition.when) == ("Fermaid O", "5 g", "Pitch + 24 h")


@pytest.mark.django_db
def test_the_next_step_is_expected_when_the_current_one_runs_its_planned_time():
    batch = _batch()
    _log(batch, "pitch", _ago(67))
    _log(batch, "racking", _ago(41), dst=_tank("Tank A"))     # 41 of 120 days into Racking

    step = _next(batch).step

    assert step.name == "Sterile Filtering"
    assert step.due == _ago(41) + datetime.timedelta(days=120)
    assert step.when == "in 79 days" and not step.overdue


@pytest.mark.django_db
def test_a_step_past_its_plan_is_overdue():
    batch = _batch()
    _log(batch, "pitch", _ago(162))
    _log(batch, "racking", _ago(132), dst=_tank("Tank A"))    # 132 of 120 days

    step = _next(batch).step

    assert step.name == "Sterile Filtering" and step.overdue and step.when == "overdue by 12 days"


@pytest.mark.django_db
def test_an_unpitched_batch_without_a_plan_is_still_waiting_for_pitch():
    assert _next(_batch(plan=None)).step.name == "Pitch"


@pytest.mark.django_db
def test_a_batch_without_a_plan_has_no_next_step():
    batch = _batch(plan=None)
    _log(batch, "pitch", _ago(10))

    assert _next(batch).step is None


# ---------- The next addition ----------

@pytest.mark.django_db
def test_the_next_addition_is_the_first_one_still_ahead():
    batch = _batch()
    _log(batch, "pitch", _ago(hours=30))                      # 24 h dose is past, 48 h dose in 18 h

    addition = _next(batch).addition

    assert addition.due == _ago(hours=30) + datetime.timedelta(hours=48)
    assert addition.when == "in 18 h" and addition.amount == "5 g"


@pytest.mark.django_db
def test_no_addition_once_all_are_past():
    batch = _batch()
    _log(batch, "pitch", _ago(5))

    assert _next(batch).addition is None


# ---------- Nothing to show ----------

@pytest.mark.django_db
def test_a_completed_batch_has_nothing_next():
    batch = _batch(plan=None, additions=())
    _log(batch, "pitch", _ago(30))
    _log(batch, "racking", _ago(20), dst=_tank("Tank A"))
    transition_stage_event(batch, _stage("sterile-filtering"), timestamp=_ago(2), packaging=PlanStep.VESSEL_BOTTLES)
    _log(batch, "complete-batch", _ago(1))

    nxt = _next(batch)
    assert nxt.step is None and nxt.addition is None and not nxt


# ---------- On the batch page ----------

def _page(batch):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client.get(reverse("batch", kwargs={"pk": batch.pk})).content.decode()


@pytest.mark.django_db
def test_batch_page_shows_whats_next_linked_to_stage_transition():
    batch = _batch()
    _log(batch, "pitch", _ago(hours=30))

    page = _page(batch)

    strip = re.search(r'<div class="cl-next".*?</div>', page, re.DOTALL).group(0)   # wherever it sits on the page
    assert "Next" in strip and "Racking" in strip and "Fermaid O" in strip and "5 g" in strip
    assert reverse("addDetailStage", kwargs={"pk": batch.pk}) in strip


@pytest.mark.django_db
def test_batch_page_hides_the_line_when_nothing_is_next():
    batch = _batch(plan=None, additions=())
    _log(batch, "pitch", _ago(30))

    assert 'class="cl-next"' not in _page(batch)
