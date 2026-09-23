import datetime

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from ..factories import BatchFactory, FermenterFactory, VesselFactory
from ..models import AgingTank, BatchStage, Vessel
from ..services import transition_stage_event


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


def _stage(shortid):
    return BatchStage.objects.get(shortid=shortid)


def _ago(days):
    return timezone.now() - datetime.timedelta(days=days)


def _batch():
    vessel = VesselFactory(name="Carboy 1", status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(fermenter=FermenterFactory(vessel=vessel))
    batch.vessel = vessel
    batch.startdate = _ago(120)
    batch.save()
    return batch


def _tank(name):
    vessel = VesselFactory(name=name, status=Vessel.STATUS_READY)
    AgingTank.objects.create(vessel=vessel)
    return vessel


def _step(batch, shortid, days_ago, dst=None, notes=""):
    return transition_stage_event(batch, _stage(shortid), timestamp=_ago(days_ago), dst_vessel=dst, notes=notes)


def _page(client, batch):
    return client.get(reverse("batch", kwargs={"pk": batch.pk}))


# ---------- "Log stage" link ----------

@pytest.mark.django_db
def test_active_batch_page_links_to_log_stage(client):
    batch = _batch()

    page = _page(client, batch).content.decode()

    assert reverse("addDetailStage", kwargs={"pk": batch.pk}) in page


@pytest.mark.django_db
def test_completed_batch_page_has_no_log_stage_link(client):
    batch = _batch()
    _step(batch, "pitch", 100)
    _step(batch, "racking", 80, dst=_tank("Tank A"))
    _step(batch, "sterile-filtering", 40, dst=_tank("Tank B"))
    _step(batch, "complete-batch", 30)

    page = _page(client, batch).content.decode()

    assert reverse("addDetailStage", kwargs={"pk": batch.pk}) not in page
    assert "Completed" in page


# ---------- Before Pitch ----------

@pytest.mark.django_db
def test_unpitched_batch_shows_an_empty_timeline(client):
    response = _page(client, _batch())

    page = response.content.decode()
    assert "Stage timeline" in page
    assert "Not pitched" in page
    assert "No stages logged yet." in page
    assert response.context["vessel_stays"] == []
    assert response.context["full_aging"] is None


# ---------- Time in vessel ----------

@pytest.mark.django_db
def test_time_in_vessel_table_lists_each_stay_with_the_current_one_marked(client):
    batch = _batch()
    _step(batch, "pitch", 100, notes="EC-1118")
    _step(batch, "racking", 86, dst=_tank("Tank A"))
    _step(batch, "racking", 56, dst=_tank("Tank B"))

    response = _page(client, batch)

    stays = response.context["vessel_stays"]
    assert [(s.event.stage.name, s.vessel.name) for s in stays] == [
        ("Pitch", "Carboy 1"), ("Racking", "Tank A"), ("Racking", "Tank B"),
    ]
    page = response.content.decode()
    assert page.count('class="cl-stage cl-stage--active">Current</span>') == 1
    for vessel in (s.vessel for s in stays):
        assert reverse("vessel", kwargs={"pk": vessel.pk}) in page


@pytest.mark.django_db
def test_stage_notes_show_as_a_hover_tooltip(client):
    batch = _batch()
    _step(batch, "pitch", 100, notes="EC-1118")

    page = _page(client, batch).content.decode()

    assert 'js-mouse-tooltip" data-toggle="tooltip" title="EC-1118"' in page


# ---------- Full aging ----------

@pytest.mark.django_db
def test_full_aging_is_ongoing_until_a_filtering(client):
    batch = _batch()
    _step(batch, "pitch", 100)
    _step(batch, "racking", 86, dst=_tank("Tank A"))
    _step(batch, "racking", 56, dst=_tank("Tank B"))

    response = _page(client, batch)

    assert response.context["full_aging"].is_open
    assert "ongoing" in response.content.decode()


@pytest.mark.django_db
def test_full_aging_is_closed_by_the_first_filtering(client):
    batch = _batch()
    _step(batch, "pitch", 100)
    _step(batch, "racking", 86, dst=_tank("Tank A"))
    _step(batch, "coarse-filtering", 56, dst=_tank("Tank B"))

    response = _page(client, batch)

    aging = response.context["full_aging"]
    assert not aging.is_open
    assert aging.duration.days == 30


# ---------- Queries ----------

@pytest.mark.django_db
def test_timeline_does_not_add_queries_per_stage(client, django_assert_max_num_queries):
    batch = _batch()
    _step(batch, "pitch", 100)
    _step(batch, "racking", 86, dst=_tank("Tank A"))
    with django_assert_max_num_queries(200) as few:
        _page(client, batch)
    _step(batch, "racking", 70, dst=_tank("Tank B"))
    _step(batch, "racking", 60, dst=_tank("Tank C"))
    _step(batch, "coarse-filtering", 50, dst=_tank("Tank D"))
    _step(batch, "fine-filtering", 40, dst=_tank("Tank E"))

    with django_assert_max_num_queries(len(few.captured_queries)):
        _page(client, batch)


# ---------- Header ----------

@pytest.mark.django_db
def test_header_shows_the_current_vessel_after_a_transfer(client):
    batch = _batch()
    _step(batch, "pitch", 100)
    tank = _tank("Tank A")
    _step(batch, "racking", 86, dst=tank)

    page = _page(client, batch).content.decode()

    header = page[page.index('class="cl-batch-sub"'):page.index('class="cl-menu"')]
    assert f'Vessel <b><a href="{reverse("vessel", kwargs={"pk": tank.pk})}">Tank A</a></b>' in header
    assert "Carboy 1" not in header
