import pytest
from django import forms
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

from ..factories import BatchFactory, FermenterFactory, RecipeFactory, VesselFactory
from ..fields import DescriptiveQuantityField, VolumeField
from ..models import AgingTank, Batch, Vessel


class _VolumeForm(forms.Form):
    volume = VolumeField()
    optional = VolumeField(required=False)


def _clean(value, optional=""):
    form = _VolumeForm(data={"volume": value, "optional": optional})
    form.is_valid()
    return form


# ---------- VolumeField ----------

@pytest.mark.parametrize(
    "entered, magnitude, unit",
    [("6 gallons", 6, "gallon"), ("6Gallons", 6, "gallon"), ("5.5 gal", 5.5, "gallon"),
     ("20 liters", 20, "liter"), ("20 L", 20, "liter"), ("6.00 gallon", 6, "gallon")],
)
def test_volume_field_returns_the_volume_as_entered(entered, magnitude, unit):
    form = _clean(entered)

    value = form.cleaned_data["volume"]
    assert value.magnitude == pytest.approx(magnitude)
    assert str(value.units) == unit


def test_a_number_without_units_says_units_are_required():
    form = _clean("6")

    assert form.errors["volume"] == ["Units are required."]


@pytest.mark.parametrize("entered", ["6 mg/L", "6 kg", "6 days"])
def test_a_non_volume_unit_is_rejected(entered):
    form = _clean(entered)

    assert form.errors["volume"] == ["Use a volume unit: gallons or liters."]


@pytest.mark.parametrize("entered", ["six gallons", "gallons", "abc"])
def test_text_that_is_not_a_measurement_is_rejected(entered):
    form = _clean(entered)

    assert form.errors["volume"] == ["Enter an amount and a unit, e.g. 6 gallons or 20 liters."]


def test_zero_or_negative_is_rejected():
    assert "volume" in _clean("0 gallons").errors
    assert "volume" in _clean("-2 liters").errors


def test_an_optional_volume_can_be_left_blank():
    form = _clean("6 gallons", optional="")

    assert form.is_valid()
    assert form.cleaned_data["optional"] is None


def test_an_invalid_field_is_marked_for_the_red_border():
    form = _clean("6")

    assert 'aria-invalid="true"' in str(form["volume"])


# ---------- DescriptiveQuantityField ----------

def test_a_stored_value_without_a_unit_part_is_read_as_base_units():
    # Regression: this path returned None.
    field = Vessel._meta.get_field("capacity")

    value = field.from_db_value("20")

    assert value is not None
    assert value.magnitude == pytest.approx(20)
    assert str(value.units) == "liter"


# ---------- Vessel capacity / fill ----------

def test_vessel_capacity_and_fill_are_descriptive_volumes_stored_in_liters():
    for name in ("capacity", "fill"):
        field = Vessel._meta.get_field(name)
        assert isinstance(field, DescriptiveQuantityField)
        assert field.base_units == "liters"
    assert Vessel._meta.get_field("fill").null is True
    for old in ("max_size", "max_size_units", "used_size", "used_size_units"):
        assert old not in {f.name for f in Vessel._meta.get_fields()}


@pytest.mark.django_db
def test_vessel_capacity_is_returned_as_entered():
    vessel = VesselFactory(capacity="6 gallons")

    vessel.refresh_from_db()

    assert (vessel.capacity.magnitude, str(vessel.capacity.units)) == (pytest.approx(6), "gallon")
    assert vessel.fill is None
    assert str(vessel) == f"{vessel.name} (6.00 gallon)"


@pytest.mark.django_db
def test_vessel_capacity_in_liters_stays_in_liters():
    vessel = VesselFactory(capacity="20 liters", fill="15 liters")

    vessel.refresh_from_db()

    assert str(vessel.capacity) == "20.00 liter"
    assert str(vessel.fill) == "15.00 liter"


@pytest.mark.django_db
def test_vessel_page_shows_capacity_and_fill(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    vessel = VesselFactory(capacity="6 gallons", fill="5 gallons")

    page = client.get(reverse("vessel", kwargs={"pk": vessel.pk})).content.decode()

    assert "6.00 gallon" in page
    assert "5.00 gallon" in page


@pytest.mark.django_db
def test_vessels_api_reports_capacity_as_entered(db):
    api = APIClient()
    api.force_authenticate(user=get_user_model().objects.create_user(username="cellarhand", password="pw"))
    vessel = VesselFactory(name="Tank A", capacity="20 liters")
    AgingTank.objects.create(vessel=vessel)

    entry = api.get(reverse("vessel-list")).json()[0]

    assert entry["capacity"] == "20.00 liter"
    assert entry["display_name"] == "Tank A (Aging Tank, 20.00 liter)"


# ---------- Add Batch size ----------

def _add_batch(size):
    client = Client()
    fermenter = FermenterFactory(vessel=VesselFactory(status=Vessel.STATUS_READY))
    return client.post(reverse("addBatch"), {
        "name": "volume test batch", "startdate": "2026-09-01", "size": size,
        "fermenter": fermenter.pk, "startingGravity": "1.090", "estimatedEndGravity": "1.000",
        "recipe": RecipeFactory().pk,
    })


@pytest.mark.django_db
def test_add_batch_without_size_units_shows_an_error_instead_of_crashing():
    response = _add_batch("6")

    assert response.status_code == 200
    assert response.context["form"].errors["size"] == ["Units are required."]
    assert 'aria-invalid="true"' in str(response.context["form"]["size"])
    assert not Batch.objects.filter(name="volume test batch").exists()


@pytest.mark.django_db
def test_add_batch_with_a_volume_saves_it_as_entered():
    response = _add_batch("5.5 gallons")

    assert response.status_code == 302
    size = Batch.objects.get(name="volume test batch").size
    assert (size.magnitude, str(size.units)) == (pytest.approx(5.5), "gallon")


@pytest.mark.django_db
def test_batch_factory_still_builds_batches():
    assert BatchFactory().size.magnitude == pytest.approx(6)
