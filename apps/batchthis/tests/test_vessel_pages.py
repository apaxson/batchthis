import datetime

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from ..factories import BatchFactory, FermenterFactory, VesselFactory, VesselStatusEventFactory
from ..models import AgingTank, Barrel, Vessel


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="cellarhand", password="password123")


@pytest.fixture
def api_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def client(user):
    client = Client()
    client.force_login(user)
    return client


# ---------- Vessel model helpers ----------

@pytest.mark.django_db
@pytest.mark.parametrize(
    "status, badge",
    [
        (Vessel.STATUS_READY, "cl-stage--ready"),
        (Vessel.STATUS_ACTIVE, "cl-stage--active"),
        (Vessel.STATUS_DIRTY, "cl-stage--dirty"),
    ],
)
def test_status_badge_class_maps_each_status(status, badge):
    assert VesselFactory(status=status).status_badge_class == badge


@pytest.mark.django_db
def test_vessel_type_reflects_its_wrapper_model():
    fermenter_vessel = FermenterFactory().vessel
    aging_vessel = VesselFactory()
    AgingTank.objects.create(vessel=aging_vessel)
    barrel_vessel = VesselFactory()
    Barrel.objects.create(vessel=barrel_vessel, serial="B1", toastLevel="Medium")

    assert fermenter_vessel.vessel_type == "Fermenter"
    assert aging_vessel.vessel_type == "Aging Tank"
    assert barrel_vessel.vessel_type == "Barrel"
    assert VesselFactory().vessel_type == ""


@pytest.mark.django_db
def test_current_batch_is_the_active_batch_in_the_vessel():
    fermenter = FermenterFactory()
    finished = BatchFactory(fermenter=fermenter, active=False)
    current = BatchFactory(fermenter=fermenter)

    assert fermenter.vessel.current_batch == current
    assert finished != fermenter.vessel.current_batch


@pytest.mark.django_db
def test_current_batch_is_none_for_an_empty_vessel():
    assert FermenterFactory().vessel.current_batch is None


# ---------- API: vessel-list ----------

@pytest.mark.django_db
def test_vessel_list_returns_serialized_vessels(api_client):
    fermenter = FermenterFactory(vessel=VesselFactory(name="Carboy 3", status=Vessel.STATUS_ACTIVE))
    batch = BatchFactory(fermenter=fermenter)
    since = timezone.now() - datetime.timedelta(days=3)
    VesselStatusEventFactory(vessel=fermenter.vessel, status=Vessel.STATUS_ACTIVE, timestamp=since)

    response = api_client.get(reverse("vessel-list"))

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    entry = payload[0]
    assert entry["id"] == fermenter.vessel.id
    assert entry["name"] == "Carboy 3"
    assert entry["vessel_type"] == "Fermenter"
    assert entry["status"] == Vessel.STATUS_ACTIVE
    assert entry["status_class"] == "cl-stage--active"
    assert entry["current_batch"] == batch.name
    assert entry["status_since"] == timezone.localtime(since).strftime("%Y-%m-%d")
    assert entry["detail_url"] == reverse("vessel", kwargs={"pk": fermenter.vessel.pk})


@pytest.mark.django_db
def test_vessel_list_has_blank_batch_and_since_for_an_idle_vessel_with_no_history(api_client):
    VesselFactory()

    entry = api_client.get(reverse("vessel-list")).json()[0]

    assert entry["current_batch"] == ""
    assert entry["status_since"] is None


@pytest.mark.django_db
def test_vessel_list_query_count_does_not_grow_with_rows(api_client, django_assert_num_queries):
    BatchFactory()
    with CaptureQueriesContext(connection) as one_row:
        api_client.get(reverse("vessel-list"))

    for _ in range(3):
        BatchFactory()
    with django_assert_num_queries(len(one_row)):
        api_client.get(reverse("vessel-list"))


@pytest.mark.django_db
def test_vessel_list_requires_login():
    response = APIClient().get(reverse("vessel-list"))

    assert response.status_code == 403


# ---------- Pages ----------

@pytest.mark.django_db
def test_vessel_listing_page_renders(client):
    response = client.get(reverse("vesselListing"))

    assert response.status_code == 200
    assert reverse("vessel-list") in response.content.decode()


@pytest.mark.django_db
def test_vessel_detail_shows_status_current_batch_and_history_newest_first(client):
    fermenter = FermenterFactory(vessel=VesselFactory(name="Carboy 3", status=Vessel.STATUS_ACTIVE))
    batch = BatchFactory(fermenter=fermenter, name="Spring Mead")
    now = timezone.now()
    older = VesselStatusEventFactory(
        vessel=fermenter.vessel, status=Vessel.STATUS_READY, timestamp=now - datetime.timedelta(days=5)
    )
    newer = VesselStatusEventFactory(
        vessel=fermenter.vessel, status=Vessel.STATUS_ACTIVE, timestamp=now - datetime.timedelta(days=1),
        batch=batch,
    )

    response = client.get(reverse("vessel", kwargs={"pk": fermenter.vessel.pk}))

    assert response.status_code == 200
    assert response.context["current_batch"] == batch
    assert list(response.context["history"]) == [newer, older]
    html = response.content.decode()
    assert "Carboy 3" in html
    assert "Spring Mead" in html
    assert reverse("batch", kwargs={"pk": batch.pk}) in html


@pytest.mark.django_db
def test_vessel_detail_404s_for_a_missing_vessel(client):
    response = client.get(reverse("vessel", kwargs={"pk": 9999}))

    assert response.status_code == 404


@pytest.mark.django_db
def test_vessel_pages_require_login(user):
    vessel = VesselFactory()
    anon = Client()

    for url in (reverse("vesselListing"), reverse("vessel", kwargs={"pk": vessel.pk})):
        response = anon.get(url)
        assert response.status_code == 302
        assert "login" in response["Location"]


@pytest.mark.django_db
def test_batch_page_links_its_vessel_to_the_vessel_detail_page(client):
    batch = BatchFactory()

    response = client.get(reverse("batch", kwargs={"pk": batch.pk}))

    assert reverse("vessel", kwargs={"pk": batch.fermenter.vessel.pk}) in response.content.decode()


# ---------- Mark cleaned ----------

@pytest.mark.django_db
def test_mark_cleaned_moves_a_dirty_vessel_to_ready_and_logs_the_notes(client):
    vessel = VesselFactory(status=Vessel.STATUS_DIRTY)

    response = client.post(reverse("markVesselCleaned", kwargs={"pk": vessel.pk}), {"notes": "PBW + Star San"})

    assert response.status_code == 302
    assert response["Location"] == reverse("vessel", kwargs={"pk": vessel.pk})
    vessel.refresh_from_db()
    assert vessel.status == Vessel.STATUS_READY
    event = vessel.current_status_event
    assert event.status == Vessel.STATUS_READY
    assert event.notes == "PBW + Star San"
    assert event.batch is None


@pytest.mark.django_db
def test_mark_cleaned_defaults_the_note_when_left_blank(client):
    vessel = VesselFactory(status=Vessel.STATUS_DIRTY)

    client.post(reverse("markVesselCleaned", kwargs={"pk": vessel.pk}), {"notes": "  "})

    assert vessel.current_status_event.notes == "Cleaned"


@pytest.mark.django_db
@pytest.mark.parametrize("status", [Vessel.STATUS_ACTIVE, Vessel.STATUS_READY])
def test_mark_cleaned_is_rejected_unless_the_vessel_needs_cleaning(client, status):
    vessel = VesselFactory(status=status)

    response = client.post(reverse("markVesselCleaned", kwargs={"pk": vessel.pk}), follow=True)

    vessel.refresh_from_db()
    assert vessel.status == status
    assert not vessel.status_events.exists()
    assert "only a vessel that needs cleaning" in response.content.decode().lower()


@pytest.mark.django_db
def test_mark_cleaned_rejects_get(client):
    vessel = VesselFactory(status=Vessel.STATUS_DIRTY)

    response = client.get(reverse("markVesselCleaned", kwargs={"pk": vessel.pk}))

    assert response.status_code == 405
    vessel.refresh_from_db()
    assert vessel.status == Vessel.STATUS_DIRTY


@pytest.mark.django_db
def test_mark_cleaned_requires_login():
    vessel = VesselFactory(status=Vessel.STATUS_DIRTY)

    response = Client().post(reverse("markVesselCleaned", kwargs={"pk": vessel.pk}))

    assert response.status_code == 302
    assert "login" in response["Location"]
    vessel.refresh_from_db()
    assert vessel.status == Vessel.STATUS_DIRTY


@pytest.mark.django_db
def test_mark_cleaned_form_only_shows_for_a_vessel_that_needs_cleaning(client):
    dirty = VesselFactory(status=Vessel.STATUS_DIRTY)
    in_use = VesselFactory(status=Vessel.STATUS_ACTIVE)
    action = lambda v: reverse("markVesselCleaned", kwargs={"pk": v.pk})  # noqa: E731

    assert action(dirty) in client.get(reverse("vessel", kwargs={"pk": dirty.pk})).content.decode()
    assert action(in_use) not in client.get(reverse("vessel", kwargs={"pk": in_use.pk})).content.decode()
