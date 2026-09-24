import datetime

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from ..factories import FermenterFactory, VesselFactory
from ..forms import BatchAddForm
from ..models import AgingTank, Barrel, Fermenter, Vessel
from ..services import create_vessel, update_vessel


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


def _create(client, **overrides):
    data = {"vessel_type": "Fermenter", "name": "Carboy 9", "capacity": "6 gallons", "fill": "",
            "intended_use": "Primary", "last_passivation": "", "serial": "", "toast_level": ""}
    data.update(overrides)
    return client.post(reverse("addVessel"), data)


def _edit(client, vessel, **overrides):
    data = {"name": vessel.name, "capacity": str(vessel.capacity), "fill": "",
            "intended_use": vessel.intended_use, "last_passivation": "", "serial": "", "toast_level": ""}
    data.update(overrides)
    return client.post(reverse("editVessel", kwargs={"pk": vessel.pk}), data)


# ---------- create_vessel() ----------

@pytest.mark.django_db
@pytest.mark.parametrize("vessel_type, wrapper", [("Fermenter", Fermenter), ("Aging Tank", AgingTank), ("Barrel", Barrel)])
def test_create_vessel_makes_the_vessel_its_type_and_first_history_entry(vessel_type, wrapper):
    vessel = create_vessel(
        name="New One", vessel_type=vessel_type, capacity="6 gallons", intended_use="Aging",
        serial="B-17" if vessel_type == "Barrel" else "", toast_level="Medium" if vessel_type == "Barrel" else "",
    )

    vessel.refresh_from_db()
    assert vessel.vessel_type == vessel_type
    assert wrapper.objects.filter(vessel=vessel).count() == 1
    assert vessel.status == Vessel.STATUS_READY
    event = vessel.status_events.get()
    assert (event.status, event.notes) == (Vessel.STATUS_READY, "Vessel added")


@pytest.mark.django_db
def test_create_vessel_stores_type_specific_details():
    fermenter = create_vessel(name="F", vessel_type="Fermenter", capacity="6 gallons", intended_use="Primary",
                              last_passivation=datetime.date(2026, 9, 1))
    barrel = create_vessel(name="B", vessel_type="Barrel", capacity="59 gallons", intended_use="Aging",
                           serial="B-17", toast_level="Medium Plus")

    assert Fermenter.objects.get(vessel=fermenter).last_passivation == datetime.date(2026, 9, 1)
    b = Barrel.objects.get(vessel=barrel)
    assert (b.serial, b.toastLevel) == ("B-17", "Medium Plus")


@pytest.mark.django_db
def test_create_vessel_rejects_a_duplicate_name_ignoring_case():
    VesselFactory(name="Tank A")

    with pytest.raises(ValidationError, match="already exists"):
        create_vessel(name="tank a", vessel_type="Aging Tank", capacity="6 gallons", intended_use="Aging")

    assert Vessel.objects.count() == 1


# ---------- update_vessel() ----------

@pytest.mark.django_db
def test_update_vessel_changes_details_but_never_status_or_type():
    vessel = FermenterFactory(vessel=VesselFactory(name="Old", status=Vessel.STATUS_DIRTY)).vessel

    update_vessel(vessel, name="New", capacity="20 liters", intended_use="Secondary", fill="15 liters",
                  last_passivation=datetime.date(2026, 8, 1))

    vessel.refresh_from_db()
    assert (vessel.name, vessel.intended_use) == ("New", "Secondary")
    assert (str(vessel.capacity), str(vessel.fill)) == ("20.00 liter", "15.00 liter")
    assert vessel.status == Vessel.STATUS_DIRTY
    assert vessel.vessel_type == "Fermenter"
    assert not vessel.status_events.exists()
    assert Fermenter.objects.get(vessel=vessel).last_passivation == datetime.date(2026, 8, 1)


@pytest.mark.django_db
def test_update_vessel_may_keep_its_own_name():
    vessel = VesselFactory(name="Tank A")

    update_vessel(vessel, name="TANK A", capacity="6 gallons", intended_use="Aging")

    vessel.refresh_from_db()
    assert vessel.name == "TANK A"


# ---------- Add vessel page ----------

@pytest.mark.django_db
def test_add_vessel_creates_it_and_redirects_to_its_page(client):
    response = _create(client, fill="5 gallons", last_passivation="2026-09-01")

    vessel = Vessel.objects.get(name="Carboy 9")
    assert response.status_code == 302
    assert response["Location"] == reverse("vessel", kwargs={"pk": vessel.pk})
    assert (vessel.vessel_type, vessel.status) == ("Fermenter", Vessel.STATUS_READY)
    assert (str(vessel.capacity), str(vessel.fill)) == ("6.00 gallon", "5.00 gallon")


@pytest.mark.django_db
def test_a_new_clean_ready_fermenter_is_offered_on_add_batch(client):
    _create(client, name="Fresh Carboy")

    offered = [f.vessel.name for f in BatchAddForm().fields["fermenter"].queryset]
    assert "Fresh Carboy" in offered


@pytest.mark.django_db
def test_add_vessel_form_has_no_status_field(client):
    form = client.get(reverse("addVessel")).context["form"]

    assert "status" not in form.fields
    assert [value for value, _ in form.fields["vessel_type"].choices] == ["Fermenter", "Aging Tank", "Barrel"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "overrides, field, message",
    [
        ({"capacity": "6"}, "capacity", "Units are required."),
        ({"capacity": ""}, "capacity", "This field is required."),
        ({"fill": "7 gallons"}, "fill", "Fill can't be more than the capacity (6.00 gallon)."),
        ({"fill": "5"}, "fill", "Units are required."),
        ({"name": ""}, "name", "This field is required."),
        ({"vessel_type": "Barrel"}, "serial", "Required for a barrel."),
        ({"vessel_type": "Barrel", "serial": "B-1"}, "toast_level", "Required for a barrel."),
    ],
)
def test_add_vessel_shows_field_errors_and_saves_nothing(client, overrides, field, message):
    response = _create(client, **overrides)

    assert response.status_code == 200
    assert response.context["form"].errors[field] == [message]
    assert not Vessel.objects.exists()


@pytest.mark.django_db
def test_add_vessel_rejects_a_duplicate_name(client):
    VesselFactory(name="Carboy 9")

    response = _create(client, name="CARBOY 9")

    assert response.context["form"].errors["name"] == ["A vessel named 'Carboy 9' already exists."]
    assert Vessel.objects.count() == 1


# ---------- Edit vessel page ----------

@pytest.mark.django_db
def test_edit_vessel_prefills_and_hides_the_type_choice(client):
    vessel = VesselFactory(name="Tank A", capacity="20 liters", fill="10 liters")
    Barrel.objects.create(vessel=vessel, serial="B-3", toastLevel="Light")

    response = client.get(reverse("editVessel", kwargs={"pk": vessel.pk}))

    form = response.context["form"]
    assert "vessel_type" not in form.fields
    assert (form["name"].value(), str(form["capacity"].value())) == ("Tank A", "20.00 liter")
    assert (form["serial"].value(), form["toast_level"].value()) == ("B-3", "Light")
    assert "Barrel" in response.content.decode()


@pytest.mark.django_db
def test_edit_vessel_saves_changes_and_redirects(client):
    vessel = FermenterFactory(vessel=VesselFactory(name="Tank A")).vessel

    response = _edit(client, vessel, name="Tank A2", capacity="7 gallons")

    assert response.status_code == 302
    vessel.refresh_from_db()
    assert (vessel.name, str(vessel.capacity)) == ("Tank A2", "7.00 gallon")


@pytest.mark.django_db
def test_edit_vessel_rejects_another_vessels_name(client):
    VesselFactory(name="Tank B")
    vessel = VesselFactory(name="Tank A")

    response = _edit(client, vessel, name="tank b")

    assert "name" in response.context["form"].errors


# ---------- Links, access ----------

@pytest.mark.django_db
def test_vessels_list_links_to_add_vessel(client):
    assert reverse("addVessel") in client.get(reverse("vesselListing")).content.decode()


@pytest.mark.django_db
def test_vessel_page_menu_links_to_edit_and_all_vessels(client):
    vessel = VesselFactory()

    page = client.get(reverse("vessel", kwargs={"pk": vessel.pk})).content.decode()

    assert 'data-cl-menu' in page
    assert reverse("editVessel", kwargs={"pk": vessel.pk}) in page
    assert reverse("vesselListing") in page


@pytest.mark.django_db
def test_vessel_forms_require_login_and_404_for_a_missing_vessel(client):
    for url in (reverse("addVessel"), reverse("editVessel", kwargs={"pk": VesselFactory().pk})):
        response = Client().get(url)
        assert response.status_code == 302 and "login" in response["Location"]
    assert client.get(reverse("editVessel", kwargs={"pk": 9999})).status_code == 404
