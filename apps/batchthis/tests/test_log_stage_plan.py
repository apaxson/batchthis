"""Step 11d: Stage transition suggests the next planned step; Sterile Filtering can package into Bottles / Kegs."""
import datetime
import html
import json
import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from ..factories import BatchFactory, FermenterFactory, RecipeFactory, VesselFactory
from ..models import AgingTank, BatchStage, PlanStep, Vessel
from ..services import copy_plan_to_batch, save_recipe_plan, transition_stage_event

FERMENTER, AGING_TANK, BARREL, CURRENT = (
    Vessel.TYPE_FERMENTER, Vessel.TYPE_AGING_TANK, Vessel.TYPE_BARREL, PlanStep.VESSEL_CURRENT,
)
BOTTLES = PlanStep.VESSEL_BOTTLES
PLAN = [("pitch", "14 days", FERMENTER), ("racking", "30 days", AGING_TANK),
        ("sterile-filtering", "5 days", BOTTLES), ("complete-batch", None, CURRENT)]


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
    batch.startdate = _ago(100)
    batch.save()
    if plan:
        copy_plan_to_batch(batch)
    return batch


def _log(batch, shortid, days_ago, dst=None):
    return transition_stage_event(batch, _stage(shortid), timestamp=_ago(days_ago), dst_vessel=dst)


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


def _url(batch):
    return reverse("addDetailStage", kwargs={"pk": batch.pk})


def _post(client, batch, shortid, **data):
    payload = {"stage": _stage(shortid).pk, "timestamp": timezone.localtime(_ago(1)).strftime("%Y-%m-%dT%H:%M"),
               "dst_vessel": "", "packaging": "", "notes": ""}
    payload.update(data)
    return client.post(_url(batch), payload)


# ---------- The next planned stage is suggested ----------

@pytest.mark.django_db
def test_an_unpitched_planned_batch_suggests_pitch(client):
    form = client.get(_url(_batch())).context["form"]

    assert form["stage"].value() == _stage("pitch").pk


@pytest.mark.django_db
def test_the_next_planned_stage_is_preselected_with_a_hint(client):
    batch = _batch()
    _log(batch, "pitch", 20)

    response = client.get(_url(batch))

    assert response.context["form"]["stage"].value() == _stage("racking").pk
    page = html.unescape(response.content.decode())
    assert "Planned next: Racking into any Aging Tank (~30 days planned)" in page


@pytest.mark.django_db
def test_the_page_knows_each_stages_planned_vessel_type(client):
    batch = _batch()
    _log(batch, "pitch", 20)

    page = client.get(_url(batch)).content.decode()

    plans = json.loads(html.unescape(re.search(r"data-stage-plans='([^']*)'", page).group(1)))
    assert list(plans) == [str(_stage("racking").pk)]            # only stages allowed next
    assert plans[str(_stage("racking").pk)]["vessel_type"] == AGING_TANK
    assert 'data-prefer-type' in page

    _log(batch, "racking", 10, dst=_tank("Tank A"))
    page = client.get(_url(batch)).content.decode()
    plans = json.loads(html.unescape(re.search(r"data-stage-plans='([^']*)'", page).group(1)))
    sterile = plans[str(_stage("sterile-filtering").pk)]
    assert (sterile["vessel_type"], sterile["packaging"]) == ("", BOTTLES)


@pytest.mark.django_db
def test_a_batch_without_a_plan_suggests_nothing(client):
    batch = _batch(plan=None)

    response = client.get(_url(batch))

    assert response.context["form"]["stage"].value() is None
    assert "Planned next" not in response.content.decode()


# ---------- Sterile Filtering into Bottles / Kegs ----------

@pytest.mark.django_db
def test_planned_packaging_is_preselected(client):
    batch = _batch()
    _log(batch, "pitch", 60)
    _log(batch, "racking", 40, dst=_tank("Tank A"))

    form = client.get(_url(batch)).context["form"]

    assert form["stage"].value() == _stage("sterile-filtering").pk
    assert form["packaging"].value() == BOTTLES


@pytest.mark.django_db
def test_logging_sterile_filtering_into_bottles_packages_the_batch(client):
    batch = _batch()
    _log(batch, "pitch", 60)
    _log(batch, "racking", 40, dst=_tank("Tank A"))

    response = _post(client, batch, "sterile-filtering", packaging=BOTTLES)

    assert response.status_code == 302
    batch.refresh_from_db()
    assert batch.packaging == BOTTLES and batch.current_vessel is None


@pytest.mark.django_db
def test_packaging_ignores_a_leftover_vessel_choice(client):
    batch = _batch()
    _log(batch, "pitch", 60)
    _log(batch, "racking", 40, dst=_tank("Tank A"))
    leftover = _tank("Tank B")

    _post(client, batch, "sterile-filtering", packaging=BOTTLES, dst_vessel=leftover.pk)

    batch.refresh_from_db()
    leftover.refresh_from_db()
    assert batch.packaging == BOTTLES and leftover.status == Vessel.STATUS_READY


@pytest.mark.django_db
def test_sterile_filtering_needs_a_vessel_or_packaging(client):
    batch = _batch()
    _log(batch, "pitch", 60)
    _log(batch, "racking", 40, dst=_tank("Tank A"))

    response = _post(client, batch, "sterile-filtering")

    assert response.context["form"].errors["dst_vessel"] == [
        "Sterile Filtering transfers the batch - choose a clean, ready destination vessel or Bottles / Kegs."]


@pytest.mark.django_db
def test_other_stages_cannot_package(client):
    batch = _batch()
    _log(batch, "pitch", 20)

    response = _post(client, batch, "racking", packaging=BOTTLES, dst_vessel=_tank("Tank A").pk)

    assert response.context["form"].errors["packaging"] == ["Only Sterile Filtering can package the batch."]
    assert batch.stage_events.count() == 1


# ---------- Vessels API: a packaged batch isn't in its starting fermenter ----------

@pytest.mark.django_db
def test_vessels_api_does_not_list_a_packaged_batch_in_its_starting_fermenter(client):
    batch = _batch()
    _log(batch, "pitch", 60)
    _log(batch, "racking", 40, dst=_tank("Tank A"))
    _post(client, batch, "sterile-filtering", packaging=BOTTLES)

    rows = {row["name"]: row for row in client.get(reverse("vessel-list")).json()}

    assert rows["Carboy 1"]["current_batch"] == "" and rows["Tank A"]["current_batch"] == ""
