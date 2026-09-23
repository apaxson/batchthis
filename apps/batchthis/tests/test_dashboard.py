import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from ..factories import BatchFactory, FermenterFactory, VesselFactory
from ..models import AgingTank, Vessel, VesselStatusEvent


def _dashboard():
    # base.html only renders page content for a logged-in user.
    client = Client()
    client.force_login(get_user_model().objects.get_or_create(username="cellarhand")[0])
    return client.get(reverse("index"))


def _batch_in_fermenter(name):
    vessel = VesselFactory(name=name, status=Vessel.STATUS_ACTIVE)
    return BatchFactory(fermenter=FermenterFactory(vessel=vessel))


def _aging_tank(name, status=Vessel.STATUS_READY):
    vessel = VesselFactory(name=name, status=status)
    AgingTank.objects.create(vessel=vessel)
    return vessel


@pytest.mark.django_db
def test_dashboard_shows_the_vessel_a_batch_was_transferred_into():
    batch = _batch_in_fermenter("Old Fermenter")
    batch.transfer(batch.fermenter.vessel, _aging_tank("New Aging Tank"))

    response = _dashboard()

    page = response.content.decode()
    assert "New Aging Tank" in page
    assert "Old Fermenter" not in page
    assert [row["vessel"].name for row in response.context["vessels_in_use"]] == ["New Aging Tank"]


@pytest.mark.django_db
def test_dashboard_falls_back_to_the_fermenter_for_batches_without_a_vessel_link():
    _batch_in_fermenter("Legacy Fermenter")

    response = _dashboard()

    assert "Legacy Fermenter" in response.content.decode()
    assert response.context["vessels_in_use_count"] == 1


@pytest.mark.django_db
def test_vessels_in_use_count_includes_every_vessel_type():
    _batch_in_fermenter("Fermenter A")
    moved = _batch_in_fermenter("Fermenter B")
    moved.transfer(moved.fermenter.vessel, _aging_tank("Aging Tank C"))

    response = _dashboard()

    assert response.context["vessels_in_use_count"] == 2
    names = sorted(row["vessel"].name for row in response.context["vessels_in_use"])
    assert names == ["Aging Tank C", "Fermenter A"]


@pytest.mark.django_db
def test_out_of_service_vessels_are_not_counted_or_listed():
    batch = _batch_in_fermenter("Broken Fermenter")
    # Shouldn't happen through the app (a vessel holding a batch can't go out
    # of service), but the dashboard must not count it if the data says so.
    Vessel.objects.filter(pk=batch.fermenter.vessel.pk).update(status=Vessel.STATUS_OUT)

    response = _dashboard()

    assert response.context["vessels_in_use_count"] == 0
    assert response.context["vessels_in_use"] == []


@pytest.mark.django_db
def test_completed_batches_are_not_counted():
    _batch_in_fermenter("Finished Fermenter").complete()

    response = _dashboard()

    assert response.context["vessels_in_use_count"] == 0


def test_vessel_models_are_not_editable_in_django_admin():
    # Status changes must go through services.set_vessel_status() so the
    # history log stays complete; admin edits would bypass it.
    assert not admin.site.is_registered(Vessel)
    assert not admin.site.is_registered(VesselStatusEvent)
