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


def _tank(name, status=Vessel.STATUS_READY):
    vessel = VesselFactory(name=name, status=status)
    AgingTank.objects.create(vessel=vessel)
    return vessel


def _aging_batch():
    vessel = VesselFactory(name="Carboy 1", status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(fermenter=FermenterFactory(vessel=vessel))
    batch.vessel = vessel
    batch.startdate = _ago(120)
    batch.save()
    transition_stage_event(batch, _stage("pitch"), timestamp=_ago(100))
    transition_stage_event(batch, _stage("racking"), timestamp=_ago(80), dst_vessel=_tank("Tank A"))
    return batch


def _url(batch):
    return reverse("transferBatch", kwargs={"pk": batch.pk})


def _post(client, batch, dst=None, reason="Pump failure", stage=None, when=None):
    data = {"reason": reason, "timestamp": timezone.localtime(when or _ago(1)).strftime("%Y-%m-%dT%H:%M")}
    if dst is not None:
        data["dst_vessel"] = dst.pk
    if stage is not None:
        data["stage"] = _stage(stage).pk
    return client.post(_url(batch), data)


# ---------- Access ----------

@pytest.mark.django_db
def test_transfer_page_requires_login():
    batch = _aging_batch()

    for response in (Client().get(_url(batch)), Client().post(_url(batch), {"reason": "x"})):
        assert response.status_code == 302 and "login" in response["Location"]


@pytest.mark.django_db
def test_transfer_page_404s_for_a_missing_batch(client):
    assert client.get(reverse("transferBatch", kwargs={"pk": 9999})).status_code == 404


# ---------- GET ----------

@pytest.mark.django_db
def test_stage_choices_default_to_no_change_plus_the_transferring_next_stages(client):
    batch = _aging_batch()

    response = client.get(_url(batch))

    field = response.context["form"].fields["stage"]
    assert field.empty_label == "No stage change (stay in Aging)"
    assert [s.shortid for s in field.queryset] == [
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
def test_transfer_page_points_to_the_normal_workflow(client):
    batch = _aging_batch()

    page = client.get(_url(batch)).content.decode()

    assert reverse("addDetailStage", kwargs={"pk": batch.pk}) in page


# ---------- POST ----------

@pytest.mark.django_db
def test_a_transfer_redirects_to_the_batch_page_and_moves_the_batch(client):
    batch = _aging_batch()
    dst = _tank("Tank B")

    response = _post(client, batch, dst=dst)

    assert response.status_code == 302
    assert response["Location"] == reverse("batch", kwargs={"pk": batch.pk})
    batch.refresh_from_db()
    assert batch.vessel == dst
    assert batch.stage_events.latest("pk").stage is None


@pytest.mark.django_db
def test_a_transfer_with_a_stage_logs_that_stage(client):
    batch = _aging_batch()

    _post(client, batch, dst=_tank("Tank B"), stage="coarse-filtering", reason="Clarity issue")

    event = batch.stage_events.latest("pk")
    assert (event.stage.shortid, event.notes) == ("coarse-filtering", "Clarity issue")


@pytest.mark.django_db
def test_a_missing_reason_is_a_form_error(client):
    batch = _aging_batch()

    response = _post(client, batch, dst=_tank("Tank B"), reason="  ")

    assert response.status_code == 200
    assert "reason" in response.context["form"].errors
    assert batch.stage_events.count() == 2


@pytest.mark.django_db
def test_a_missing_destination_is_a_form_error(client):
    batch = _aging_batch()

    response = _post(client, batch)

    assert "dst_vessel" in response.context["form"].errors


@pytest.mark.django_db
def test_a_destination_that_is_not_clean_ready_is_a_form_error(client):
    batch = _aging_batch()

    response = _post(client, batch, dst=_tank("Busy Tank", status=Vessel.STATUS_ACTIVE))

    assert "dst_vessel" in response.context["form"].errors
    assert batch.stage_events.count() == 2


@pytest.mark.django_db
def test_service_errors_show_on_the_form(client):
    batch = _aging_batch()

    response = _post(client, batch, dst=_tank("Tank B"), when=_ago(90))  # before the Racking

    assert response.status_code == 200
    assert any("earlier" in e for e in response.context["form"].non_field_errors())


@pytest.mark.django_db
def test_a_completed_batch_cannot_be_transferred(client):
    batch = _aging_batch()
    transition_stage_event(batch, _stage("sterile-filtering"), timestamp=_ago(60), dst_vessel=_tank("Tank B"))
    transition_stage_event(batch, _stage("complete-batch"), timestamp=_ago(55))

    page = client.get(_url(batch)).content.decode()
    response = _post(client, batch, dst=_tank("Tank C"))

    assert "is complete" in page
    assert response.status_code == 200
    assert batch.stage_events.count() == 4


# ---------- Batch page ----------

@pytest.mark.django_db
def test_batch_page_offers_transfer_only_while_active(client):
    batch = _aging_batch()

    assert _url(batch) in client.get(reverse("batch", kwargs={"pk": batch.pk})).content.decode()

    transition_stage_event(batch, _stage("sterile-filtering"), timestamp=_ago(60), dst_vessel=_tank("Tank B"))
    transition_stage_event(batch, _stage("complete-batch"), timestamp=_ago(55))
    assert _url(batch) not in client.get(reverse("batch", kwargs={"pk": batch.pk})).content.decode()


@pytest.mark.django_db
def test_batch_timeline_shows_a_transfer_row_with_the_reason_as_tooltip(client):
    batch = _aging_batch()
    _post(client, batch, dst=_tank("Tank B"), reason="Pump failure")

    page = client.get(reverse("batch", kwargs={"pk": batch.pk})).content.decode()

    assert 'js-mouse-tooltip" data-toggle="tooltip" title="Pump failure">Transfer</td>' in page
