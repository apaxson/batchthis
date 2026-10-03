"""PROGRESS BAR build stage 1: the batch page shows the progress bar (includes/_progress_bar.html)."""
import datetime
import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from ..factories import BatchFactory, FermenterFactory, RecipeFactory, VesselFactory
from ..models import AgingTank, BatchStage, PlanStep, Vessel
from ..services import copy_plan_to_batch, save_recipe_plan, transition_stage_event

PLAN = [("pitch", "30 days", Vessel.TYPE_FERMENTER), ("racking", "120 days", Vessel.TYPE_AGING_TANK),
        ("sterile-filtering", "14 days", PlanStep.VESSEL_BOTTLES), ("complete-batch", None, PlanStep.VESSEL_CURRENT)]


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


def _stage(shortid):
    return BatchStage.objects.get(shortid=shortid)


def _ago(days):
    return timezone.now() - datetime.timedelta(days=days)


def _tank(name):
    vessel = VesselFactory(name=name, status=Vessel.STATUS_READY)
    AgingTank.objects.create(vessel=vessel)
    return vessel


def _batch(plan=PLAN):
    carboy = VesselFactory(name="Carboy 1", status=Vessel.STATUS_ACTIVE)
    recipe = RecipeFactory()
    if plan:
        save_recipe_plan(recipe, [{"stage": _stage(s), "planned_duration": d, "vessel_type": v, "notes": ""}
                                  for s, d, v in plan])
    batch = BatchFactory(recipe=recipe, fermenter=FermenterFactory(vessel=carboy), vessel=carboy)
    batch.startdate = _ago(400)
    batch.save()
    if plan:
        copy_plan_to_batch(batch)
    return batch


def _log(batch, shortid, days_ago, dst=None, packaging=""):
    transition_stage_event(batch, _stage(shortid), timestamp=_ago(days_ago), dst_vessel=dst, packaging=packaging)


def _progress_text(client, batch):
    """The progress bar's text, tags stripped and whitespace collapsed ("" when it isn't drawn)."""
    html = client.get(reverse("batch", kwargs={"pk": batch.pk})).content.decode()
    match = re.search(r'<div class="cl-progress">(.*?)<!-- end progress -->', html, re.DOTALL)
    if not match:
        return ""
    return " ".join(re.sub(r"<[^>]+>", " ", match.group(1)).replace("&middot;", "·").split())


@pytest.mark.django_db
def test_page_shows_percent_of_plan_totals_and_today(client):
    batch = _batch()
    _log(batch, "pitch", 67)
    _log(batch, "racking", 41, dst=_tank("Tank A"))

    text = _progress_text(client, batch)

    assert "41% of plan" in text
    assert "67 d elapsed" in text and "160 d expected total" in text and "93 d remaining" in text
    assert "Fermentation 26 d · plan 30" in text
    assert "Aging 41 of 120 d" in text
    assert "Bottling 14 d planned" in text
    assert "Today · 41% of plan 93 d left" in text
    assert "over plan" not in text


@pytest.mark.django_db
def test_page_flags_a_stage_past_its_plan(client):
    batch = _batch()
    _log(batch, "pitch", 162)
    _log(batch, "racking", 132, dst=_tank("Tank A"))

    text = _progress_text(client, batch)

    assert "Aging 132 of 120 d · +12 over" in text
    assert "Aging: 12 days over plan" in text


@pytest.mark.django_db
def test_page_without_a_plan_shows_elapsed_time_only(client):
    batch = _batch(plan=None)
    _log(batch, "pitch", 87)
    _log(batch, "racking", 49, dst=_tank("Tank A"))

    text = _progress_text(client, batch)

    assert "87 d elapsed" in text and "Today · day 87" in text
    assert "of plan" not in text and "remaining" not in text and "Bottling" not in text


@pytest.mark.django_db
def test_page_for_a_completed_batch(client):
    batch = _batch()
    _log(batch, "pitch", 168)
    _log(batch, "racking", 140, dst=_tank("Tank A"))
    _log(batch, "sterile-filtering", 9, packaging=PlanStep.VESSEL_BOTTLES)
    _log(batch, "complete-batch", 0)

    text = _progress_text(client, batch)

    assert "100% of plan" in text and "Completed · 168 d" in text
    assert "Today" not in text


@pytest.mark.django_db
def test_page_for_an_unpitched_batch_with_a_plan(client):
    text = _progress_text(client, _batch())

    assert "0% of plan" in text and "164 d planned" in text and "Not pitched" in text
    assert "Today" not in text


@pytest.mark.django_db
def test_no_bar_for_an_unpitched_batch_without_a_plan(client):
    assert _progress_text(client, _batch(plan=None)) == ""


# ---------- Build stage 2: vessels and steps on the bar ----------

def _progress_html(client, batch):
    html = client.get(reverse("batch", kwargs={"pk": batch.pk})).content.decode()
    return re.search(r'<div class="cl-progress">(.*?)<!-- end progress -->', html, re.DOTALL).group(1)


@pytest.mark.django_db
def test_page_shows_vessel_stays_and_steps_on_the_bar(client):
    batch = _batch()
    _log(batch, "pitch", 67)
    tank = _tank("Tank A")
    _log(batch, "racking", 41, dst=tank)

    text = _progress_text(client, batch)
    html = _progress_html(client, batch)

    assert "Vessels Carboy 1 26 d Tank A 41 d Bottles 14 d" in text
    assert f'href="{reverse("vessel", kwargs={"pk": tank.pk})}"' in html
    assert "cl-progress-stay-tail" in html                       # Tank A's planned rest, dashed
    for step in ("Pitch", "Racking", "Sterile Filtering", "Complete Batch"):
        assert step in text
    assert "Full aging 41 d so far" in text


@pytest.mark.django_db
def test_page_for_a_completed_batch_shows_packaging(client):
    batch = _batch()
    _log(batch, "pitch", 168)
    _log(batch, "racking", 140, dst=_tank("Tank A"))
    _log(batch, "sterile-filtering", 9, packaging=PlanStep.VESSEL_BOTTLES)
    _log(batch, "complete-batch", 0)

    text = _progress_text(client, batch)
    html = _progress_html(client, batch)

    assert "Tank A 131 d Bottles 9 d" in text
    assert "cl-progress-stay--planned" not in html and "cl-progress-step--ahead" not in html
