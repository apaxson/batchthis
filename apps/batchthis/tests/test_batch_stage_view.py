import datetime

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from ..factories import BatchFactory, FermenterFactory, VesselFactory
from ..models import AgingTank, BatchStage, Vessel
from ..services import allowed_next_stages, transition_stage_event


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="cellarhand", password="password123")


@pytest.fixture
def client(user):
    client = Client()
    client.force_login(user)
    return client


def _stage(shortid):
    return BatchStage.objects.get(shortid=shortid)


def _batch():
    vessel = VesselFactory(name="Carboy 1", status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(fermenter=FermenterFactory(vessel=vessel))
    batch.vessel = vessel
    batch.startdate = timezone.now() - datetime.timedelta(days=60)
    batch.save()
    return batch


def _tank(name="Aging Tank A", status=Vessel.STATUS_READY):
    vessel = VesselFactory(name=name, status=status)
    AgingTank.objects.create(vessel=vessel)
    return vessel


def _ago(days):
    return timezone.now() - datetime.timedelta(days=days)


def _url(batch):
    return reverse("addDetailStage", kwargs={"pk": batch.pk})


def _post(client, batch, shortid, dst=None, when=None, notes=""):
    data = {
        "stage": _stage(shortid).pk,
        "timestamp": timezone.localtime(when or _ago(1)).strftime("%Y-%m-%dT%H:%M"),
        "notes": notes,
    }
    if dst is not None:
        data["dst_vessel"] = dst.pk
    return client.post(_url(batch), data)


def _aging_batch():
    batch = _batch()
    transition_stage_event(batch, _stage("pitch"), timestamp=_ago(50))
    transition_stage_event(batch, _stage("racking"), timestamp=_ago(40), dst_vessel=_tank("First Tank"))
    return batch


# ---------- allowed_next_stages() ----------

@pytest.mark.django_db
def test_allowed_next_stages_follow_the_workflow():
    batch = _batch()
    assert [s.shortid for s in allowed_next_stages(batch)] == ["pitch"]

    transition_stage_event(batch, _stage("pitch"), timestamp=_ago(50))
    assert [s.shortid for s in allowed_next_stages(batch)] == ["racking"]

    transition_stage_event(batch, _stage("racking"), timestamp=_ago(40), dst_vessel=_tank("Tank A"))
    assert [s.shortid for s in allowed_next_stages(batch)] == [
        "racking", "coarse-filtering", "fine-filtering", "sterile-filtering",
    ]

    transition_stage_event(batch, _stage("sterile-filtering"), timestamp=_ago(30), dst_vessel=_tank("Tank B"))
    assert [s.shortid for s in allowed_next_stages(batch)] == ["complete-batch"]

    transition_stage_event(batch, _stage("complete-batch"), timestamp=_ago(20))
    assert allowed_next_stages(batch) == []


# ---------- GET ----------

@pytest.mark.django_db
def test_stage_page_requires_login():
    batch = _batch()

    response = Client().get(_url(batch))

    assert response.status_code == 302 and "login" in response["Location"]


@pytest.mark.django_db
def test_stage_page_404s_for_a_missing_batch(client):
    assert client.get(reverse("addDetailStage", kwargs={"pk": 9999})).status_code == 404


@pytest.mark.django_db
def test_stage_page_only_offers_the_allowed_next_stages(client):
    batch = _aging_batch()

    form = client.get(_url(batch)).context["form"]

    assert [s.shortid for s in form.fields["stage"].queryset] == [
        "racking", "coarse-filtering", "fine-filtering", "sterile-filtering",
    ]


@pytest.mark.django_db
def test_destination_choices_are_only_clean_ready_vessels(client):
    batch = _aging_batch()
    ready = _tank("Ready Tank")
    _tank("Busy Tank", status=Vessel.STATUS_ACTIVE)
    _tank("Dirty Tank", status=Vessel.STATUS_DIRTY)
    _tank("Broken Tank", status=Vessel.STATUS_OUT)

    form = client.get(_url(batch)).context["form"]

    assert list(form.fields["dst_vessel"].queryset) == [ready]


@pytest.mark.django_db
def test_stage_page_marks_which_stages_transfer_the_batch(client):
    batch = _aging_batch()

    page = client.get(_url(batch)).content.decode()

    transferring = sorted(s.pk for s in BatchStage.objects.filter(transfers_batch=True))
    assert f'data-transfer-stage-ids="{",".join(str(pk) for pk in transferring)}"' in page


@pytest.mark.django_db
def test_a_completed_batch_has_no_stages_to_log(client):
    batch = _aging_batch()
    transition_stage_event(batch, _stage("sterile-filtering"), timestamp=_ago(30), dst_vessel=_tank("Tank B"))
    transition_stage_event(batch, _stage("complete-batch"), timestamp=_ago(20))

    response = client.get(_url(batch))

    assert list(response.context["form"].fields["stage"].queryset) == []
    assert "No further stages" in response.content.decode()


# ---------- POST ----------

@pytest.mark.django_db
def test_logging_pitch_redirects_to_the_batch_page(client):
    batch = _batch()

    response = _post(client, batch, "pitch", notes="EC-1118")

    assert response.status_code == 302
    assert response["Location"] == reverse("batch", kwargs={"pk": batch.pk})
    event = batch.stage_events.get()
    assert (event.stage.shortid, event.notes) == ("pitch", "EC-1118")


@pytest.mark.django_db
def test_logging_racking_into_a_clean_ready_vessel_transfers_the_batch(client):
    batch = _aging_batch()
    dst = _tank("Second Tank")

    response = _post(client, batch, "racking", dst=dst)

    assert response.status_code == 302
    batch.refresh_from_db()
    dst.refresh_from_db()
    assert batch.vessel == dst
    assert dst.status == Vessel.STATUS_ACTIVE


@pytest.mark.django_db
@pytest.mark.parametrize("status", [Vessel.STATUS_ACTIVE, Vessel.STATUS_DIRTY, Vessel.STATUS_OUT])
def test_a_destination_that_is_not_clean_ready_is_rejected(client, status):
    batch = _aging_batch()
    not_ready = _tank("Not Ready", status=status)

    response = _post(client, batch, "racking", dst=not_ready)

    assert response.status_code == 200
    assert "dst_vessel" in response.context["form"].errors
    assert batch.stage_events.count() == 2
    not_ready.refresh_from_db()
    assert not_ready.status == status


@pytest.mark.django_db
def test_a_transferring_stage_without_a_destination_shows_an_error(client):
    batch = _aging_batch()

    response = _post(client, batch, "racking")

    assert response.status_code == 200
    assert "dst_vessel" in response.context["form"].errors
    assert batch.stage_events.count() == 2


@pytest.mark.django_db
def test_a_stage_that_is_not_allowed_now_is_rejected(client):
    batch = _batch()  # not pitched yet

    response = _post(client, batch, "sterile-filtering", dst=_tank())

    assert response.status_code == 200
    assert "stage" in response.context["form"].errors
    assert not batch.stage_events.exists()


@pytest.mark.django_db
def test_workflow_errors_from_the_service_are_shown_on_the_form(client):
    batch = _batch()

    response = _post(client, batch, "pitch", when=timezone.now() + datetime.timedelta(days=1))

    assert response.status_code == 200
    assert any("future" in e for e in response.context["form"].non_field_errors())
    assert not batch.stage_events.exists()


@pytest.mark.django_db
def test_stage_post_requires_login():
    batch = _batch()

    response = Client().post(_url(batch), {"stage": _stage("pitch").pk})

    assert response.status_code == 302 and "login" in response["Location"]
    assert not batch.stage_events.exists()


# ---------- API: Clean/Ready vessels for the destination picker ----------

@pytest.mark.django_db
def test_vessel_list_can_be_filtered_to_clean_ready_vessels_with_display_names(user):
    ready = _tank("Ready Tank")
    _tank("Busy Tank", status=Vessel.STATUS_ACTIVE)
    api = APIClient()
    api.force_authenticate(user=user)

    response = api.get(reverse("vessel-list"), {"status": Vessel.STATUS_READY})

    payload = response.json()
    assert [entry["id"] for entry in payload] == [ready.pk]
    assert payload[0]["display_name"] == "Ready Tank (Aging Tank, 6.00 gallon)"
