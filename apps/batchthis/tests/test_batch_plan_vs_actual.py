"""Step 11e: after Pitch, the batch page's Plan section compares plan vs actual."""
import datetime
import html
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


def _batch():
    recipe = RecipeFactory()
    save_recipe_plan(recipe, [{"stage": _stage(s), "planned_duration": d, "vessel_type": v, "notes": ""}
                              for s, d, v in PLAN])
    carboy = VesselFactory(name="Carboy 1", status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(recipe=recipe, fermenter=FermenterFactory(vessel=carboy), vessel=carboy)
    batch.startdate = _at(-1)
    batch.save()
    copy_plan_to_batch(batch)
    return batch


def _log(batch, shortid, day, dst=None, packaging=""):
    return transition_stage_event(batch, _stage(shortid), timestamp=_at(day), dst_vessel=dst, packaging=packaging)


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


def _section(client, batch):
    page = html.unescape(client.get(reverse("batch", kwargs={"pk": batch.pk})).content.decode())
    return re.search(r'<section class="cl-plan"[^>]*>.*?</section>', page, re.DOTALL).group(0)


def _text(fragment):
    return " ".join(re.sub(r"<[^>]+>", " ", fragment).split())


def _rows(section):
    body = re.search(r"<tbody>(.*?)</tbody>", section, re.DOTALL).group(1)
    return [[_text(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)]
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.DOTALL)]


HEADERS = ["Step", "Stage", "Status", "Planned vessel", "Actual vessel", "Planned day", "Actual day",
           "Planned time", "Actual time", "+/-"]


@pytest.mark.django_db
def test_before_pitch_the_plan_shows_without_actuals(client):
    section = _section(client, _batch())

    assert "Actual day" not in section and "Planned time" in section


@pytest.mark.django_db
def test_after_pitch_each_step_shows_plan_vs_actual(client):
    batch = _batch()
    _log(batch, "pitch", 0)
    _log(batch, "racking", 17, dst=_tank("Tank A"))

    section = _section(client, batch)

    assert [_text(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", section)] == HEADERS
    rows = _rows(section)
    assert rows[0] == ["1", "Pitch", "Done", "Any Fermenter", "Carboy 1", "Day 0", "Day 0",
                       "14 days", "17 days", "+3 days"]
    assert rows[1][:7] == ["2", "Racking", "Current", "Any Aging Tank", "Tank A", "Day 14", "Day 17"]
    assert rows[1][7:] == ["30 days", "83 days so far", "+53 days"]
    assert rows[2][:3] == ["3", "Coarse Filtering", "Upcoming"]
    assert rows[2][5:] == ["Day 44", "—", "—", "—", "—"]


@pytest.mark.django_db
def test_skipped_unplanned_and_packaged_steps(client):
    batch = _batch()
    _log(batch, "pitch", 0)
    _log(batch, "racking", 14, dst=_tank("Tank A"))
    _log(batch, "racking", 30, dst=_tank("Tank B"))                 # not in the plan
    _log(batch, "fine-filtering", 44, dst=VesselFactory(name="Barrel 1", status=Vessel.STATUS_READY))
    _log(batch, "sterile-filtering", 51, packaging=BOTTLES)        # Coarse Filtering skipped

    rows = {(r[1], r[2]): r for r in _rows(_section(client, batch))}

    assert rows[("Racking", "Unplanned")][0] == "—" and rows[("Racking", "Unplanned")][3:5] == ["—", "Tank B"]
    assert rows[("Coarse Filtering", "Skipped")][4:] == ["—", "Day 44", "—", "—", "—", "—"]
    assert rows[("Fine Filtering", "Done")][7:] == ["7 days", "7 days", "On plan"]
    assert rows[("Sterile Filtering", "Current")][3:5] == ["Bottles", "Bottles"]


@pytest.mark.django_db
def test_the_footer_shows_planned_total_and_actual_time_so_far(client):
    batch = _batch()
    _log(batch, "pitch", 0)

    footer = _text(re.search(r"<tfoot>(.*?)</tfoot>", _section(client, batch), re.DOTALL).group(1))

    assert "Planned time 56 days" in footer and "Actual 100 days so far" in footer


@pytest.mark.django_db
def test_a_completed_batch_shows_actual_total_without_so_far(client):
    batch = _batch()
    _log(batch, "pitch", 0)
    _log(batch, "racking", 14, dst=_tank("Tank A"))
    _log(batch, "sterile-filtering", 50, packaging=BOTTLES)
    _log(batch, "complete-batch", 60)

    section = _section(client, batch)

    footer = _text(re.search(r"<tfoot>(.*?)</tfoot>", section, re.DOTALL).group(1))
    assert "Actual 60 days" in footer and "so far" not in footer
    assert _rows(section)[-1][:3] == ["6", "Complete Batch", "Done"]


@pytest.mark.django_db
def test_a_current_step_within_its_plan_shows_time_left_not_a_difference(client):
    batch = _batch()
    _log(batch, "pitch", 95)                  # 5 days into a 14-day Pitch

    pitch = _rows(_section(client, batch))[0]

    assert pitch[2] == "Current" and pitch[8:] == ["5 days so far", "9 days left"]
