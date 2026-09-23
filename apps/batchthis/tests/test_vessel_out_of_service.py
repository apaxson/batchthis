import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from ..factories import BatchFactory, FermenterFactory, VesselFactory, VesselStatusEventFactory
from ..models import AgingTank, Vessel
from ..services import return_vessel_to_service, set_vessel_status, take_vessel_out_of_service


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="cellarhand", password="password123")


@pytest.fixture
def client(user):
    client = Client()
    client.force_login(user)
    return client


# ---------- Model ----------

@pytest.mark.django_db
def test_out_of_service_is_a_valid_status_with_its_own_badge():
    vessel = VesselFactory(status=Vessel.STATUS_OUT)

    vessel.full_clean()  # should not raise
    assert Vessel.STATUS_OUT == "Out of Service"
    assert vessel.status_badge_class == "cl-stage--out"


@pytest.mark.django_db
def test_set_vessel_status_records_the_status_it_moved_from():
    vessel = VesselFactory(status=Vessel.STATUS_DIRTY)

    event = set_vessel_status(vessel, Vessel.STATUS_READY)

    assert event.previous_status == Vessel.STATUS_DIRTY


# ---------- take_vessel_out_of_service() ----------

@pytest.mark.django_db
@pytest.mark.parametrize("status", [Vessel.STATUS_READY, Vessel.STATUS_DIRTY])
def test_take_out_of_service_from_ready_or_dirty_logs_the_reason(status):
    vessel = VesselFactory(status=status)

    event = take_vessel_out_of_service(vessel, "  Cracked valve  ")

    vessel.refresh_from_db()
    assert vessel.status == Vessel.STATUS_OUT
    assert event.status == Vessel.STATUS_OUT
    assert event.previous_status == status
    assert event.notes == "Cracked valve"


@pytest.mark.django_db
@pytest.mark.parametrize("reason", ["", "   "])
def test_take_out_of_service_requires_a_reason(reason):
    vessel = VesselFactory(status=Vessel.STATUS_READY)

    with pytest.raises(ValidationError):
        take_vessel_out_of_service(vessel, reason)

    vessel.refresh_from_db()
    assert vessel.status == Vessel.STATUS_READY
    assert not vessel.status_events.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("status", [Vessel.STATUS_ACTIVE, Vessel.STATUS_OUT])
def test_take_out_of_service_is_rejected_when_in_use_or_already_out(status):
    vessel = VesselFactory(status=status)

    with pytest.raises(ValidationError):
        take_vessel_out_of_service(vessel, "Cracked valve")

    vessel.refresh_from_db()
    assert vessel.status == status
    assert not vessel.status_events.exists()


@pytest.mark.django_db
def test_take_out_of_service_is_rejected_while_a_batch_is_still_in_the_vessel():
    # Status says dirty, but an active batch still sits in it - don't orphan it.
    vessel = VesselFactory(status=Vessel.STATUS_DIRTY)
    BatchFactory(fermenter=FermenterFactory(vessel=vessel))

    with pytest.raises(ValidationError):
        take_vessel_out_of_service(vessel, "Cracked valve")

    vessel.refresh_from_db()
    assert vessel.status == Vessel.STATUS_DIRTY


# ---------- return_vessel_to_service() ----------

@pytest.mark.django_db
@pytest.mark.parametrize("status", [Vessel.STATUS_READY, Vessel.STATUS_DIRTY])
def test_return_to_service_restores_the_status_it_had_before(status):
    vessel = VesselFactory(status=status)
    take_vessel_out_of_service(vessel, "Cracked valve")

    event = return_vessel_to_service(vessel)

    vessel.refresh_from_db()
    assert vessel.status == status
    assert event.status == status
    assert event.previous_status == Vessel.STATUS_OUT


@pytest.mark.django_db
def test_return_to_service_uses_the_most_recent_out_of_service_event():
    vessel = VesselFactory(status=Vessel.STATUS_READY)
    take_vessel_out_of_service(vessel, "First repair")
    return_vessel_to_service(vessel)
    set_vessel_status(vessel, Vessel.STATUS_DIRTY)
    take_vessel_out_of_service(vessel, "Second repair")

    return_vessel_to_service(vessel)

    vessel.refresh_from_db()
    assert vessel.status == Vessel.STATUS_DIRTY


@pytest.mark.django_db
def test_return_to_service_defaults_to_needs_cleaning_when_the_prior_status_is_unknown():
    # e.g. status was hand-set in Django admin, so no event says where it came from.
    vessel = VesselFactory(status=Vessel.STATUS_OUT)

    return_vessel_to_service(vessel)

    vessel.refresh_from_db()
    assert vessel.status == Vessel.STATUS_DIRTY


@pytest.mark.django_db
def test_return_to_service_ignores_an_out_of_service_event_with_no_recorded_prior_status():
    vessel = VesselFactory(status=Vessel.STATUS_OUT)
    VesselStatusEventFactory(vessel=vessel, status=Vessel.STATUS_OUT, previous_status="")

    return_vessel_to_service(vessel)

    vessel.refresh_from_db()
    assert vessel.status == Vessel.STATUS_DIRTY


@pytest.mark.django_db
@pytest.mark.parametrize("status", [Vessel.STATUS_READY, Vessel.STATUS_ACTIVE, Vessel.STATUS_DIRTY])
def test_return_to_service_is_rejected_unless_out_of_service(status):
    vessel = VesselFactory(status=status)

    with pytest.raises(ValidationError):
        return_vessel_to_service(vessel)

    assert not vessel.status_events.exists()


# ---------- Views ----------

@pytest.mark.django_db
def test_take_out_of_service_view_logs_the_reason_in_the_status_history(client):
    vessel = VesselFactory(status=Vessel.STATUS_DIRTY)

    response = client.post(reverse("takeVesselOutOfService", kwargs={"pk": vessel.pk}), {"reason": "Cracked valve"})

    assert response.status_code == 302
    assert response["Location"] == reverse("vessel", kwargs={"pk": vessel.pk})
    vessel.refresh_from_db()
    assert vessel.status == Vessel.STATUS_OUT
    page = client.get(reverse("vessel", kwargs={"pk": vessel.pk})).content.decode()
    assert "Cracked valve" in page


@pytest.mark.django_db
def test_take_out_of_service_view_without_a_reason_shows_an_error(client):
    vessel = VesselFactory(status=Vessel.STATUS_READY)

    response = client.post(
        reverse("takeVesselOutOfService", kwargs={"pk": vessel.pk}), {"reason": " "}, follow=True
    )

    vessel.refresh_from_db()
    assert vessel.status == Vessel.STATUS_READY
    assert any("reason" in str(m).lower() for m in response.context["messages"])


@pytest.mark.django_db
def test_take_out_of_service_view_rejects_an_in_use_vessel(client):
    vessel = VesselFactory(status=Vessel.STATUS_ACTIVE)

    response = client.post(
        reverse("takeVesselOutOfService", kwargs={"pk": vessel.pk}), {"reason": "Cracked valve"}, follow=True
    )

    vessel.refresh_from_db()
    assert vessel.status == Vessel.STATUS_ACTIVE
    assert list(response.context["messages"])


@pytest.mark.django_db
def test_return_to_service_view_restores_the_prior_status(client):
    vessel = VesselFactory(status=Vessel.STATUS_READY)
    take_vessel_out_of_service(vessel, "Cracked valve")

    response = client.post(reverse("returnVesselToService", kwargs={"pk": vessel.pk}))

    assert response.status_code == 302
    vessel.refresh_from_db()
    assert vessel.status == Vessel.STATUS_READY


@pytest.mark.django_db
def test_return_to_service_view_rejects_a_vessel_that_is_in_service(client):
    vessel = VesselFactory(status=Vessel.STATUS_DIRTY)

    response = client.post(reverse("returnVesselToService", kwargs={"pk": vessel.pk}), follow=True)

    vessel.refresh_from_db()
    assert vessel.status == Vessel.STATUS_DIRTY
    assert list(response.context["messages"])


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["takeVesselOutOfService", "returnVesselToService"])
def test_service_actions_reject_get_and_require_login(client, url_name):
    vessel = VesselFactory(status=Vessel.STATUS_READY)
    url = reverse(url_name, kwargs={"pk": vessel.pk})

    assert client.get(url).status_code == 405
    anonymous = Client().post(url, {"reason": "x"})
    assert anonymous.status_code == 302 and "login" in anonymous["Location"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status, take_out, give_back",
    [
        (Vessel.STATUS_READY, True, False),
        (Vessel.STATUS_DIRTY, True, False),
        (Vessel.STATUS_ACTIVE, False, False),
        (Vessel.STATUS_OUT, False, True),
    ],
)
def test_vessel_page_offers_only_the_allowed_service_action(client, status, take_out, give_back):
    vessel = VesselFactory(status=status)

    page = client.get(reverse("vessel", kwargs={"pk": vessel.pk})).content.decode()

    assert (reverse("takeVesselOutOfService", kwargs={"pk": vessel.pk}) in page) is take_out
    assert (reverse("returnVesselToService", kwargs={"pk": vessel.pk}) in page) is give_back


@pytest.mark.django_db
@pytest.mark.parametrize("wrapper, prefix", [("fermenter", "wine-tank"), ("agingtank", "aging-tank")])
def test_vessel_page_shows_the_out_of_service_artwork(client, wrapper, prefix):
    if wrapper == "fermenter":
        vessel = FermenterFactory().vessel
    else:
        vessel = VesselFactory()
        AgingTank.objects.create(vessel=vessel)
    Vessel.objects.filter(pk=vessel.pk).update(status=Vessel.STATUS_OUT)

    page = client.get(reverse("vessel", kwargs={"pk": vessel.pk})).content.decode()

    assert f"batchthis/img/{prefix}-out-of-service.svg" in page
