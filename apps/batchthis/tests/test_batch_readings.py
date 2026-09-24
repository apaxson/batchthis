"""Batch test readings: ReadingField units per test type, storage, charts/rules, Add test."""
import datetime

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from ..factories import BatchFactory
from ..fields import DescriptiveQuantityField, ReadingField
from ..forms import BatchTestForm
from ..lib.faults import get_active_flags
from ..lib.utils import Utils
from ..models import BatchTest, BatchTestType


def _type(shortid):
    return BatchTestType.objects.get(shortid=shortid)


def _form(shortid, value, batch=None, description=""):
    form = BatchTestForm(data={
        "datetime": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
        "type": _type(shortid).pk, "value": value, "description": description,
    }, batch=batch)
    form.is_valid()
    return form


def _reading(batch, shortid, value, hours_ago=1):
    return BatchTest.objects.create(
        batch=batch, type=_type(shortid), value=value,
        datetime=timezone.now() - datetime.timedelta(hours=hours_ago),
    )


# ---------- Accepted units per test type (Aaron's table) ----------

@pytest.mark.django_db
@pytest.mark.parametrize(
    "shortid, entered, chart, shown",
    [
        ("specific-gravity", "1.050 sg", 1.050, "1.050"),
        ("specific-gravity", "1.050", 1.050, "1.050"),          # bare number = sg
        ("specific-gravity", "1.0503", 1.0503, "1.050"),        # refractometer output
        ("temperature", "68 °F", 68, "68 °F"),
        ("temperature", "68 F", 68, "68 °F"),                   # shorthand, not farads
        ("temperature", "68degF", 68, "68 °F"),
        ("temperature", "20 C", 68, "20 °C"),                   # charted in °F
        ("temperature", "-1 C", 30.2, "-1 °C"),                 # cold crash: below zero is fine
        ("so2", "30 ppm", 30, "30 ppm"),
        ("so2", "30 mg/L", 30, "30 mg/L"),                      # mg/L counts as ppm
        ("yan", "250 ppm", 250, "250 ppm"),
        ("ta", "6.5 g/L", 6.5, "6.5 g/L"),
        ("ph", "3.40", 3.40, "3.40"),
    ],
)
def test_each_type_accepts_its_units_and_stores_them_as_entered(shortid, entered, chart, shown):
    form = _form(shortid, entered)
    assert form.is_valid(), form.errors

    reading = form.save(commit=False)
    reading.batch = BatchFactory()
    reading.save()
    reading.refresh_from_db()

    assert reading.chart_value == pytest.approx(chart, abs=1e-6)
    assert reading.display_value == shown


@pytest.mark.django_db
@pytest.mark.parametrize(
    "shortid, entered, message",
    [
        ("specific-gravity", "1.050 ppm", "Use sg for Specific Gravity, e.g. 1.050 sg."),
        ("temperature", "68", "Units are required."),
        ("temperature", "68 ppm", "Use °F or °C for Temperature, e.g. 68 °F."),
        ("so2", "30", "Units are required."),
        ("so2", "30 g", "Use ppm or mg/L for SO2, e.g. 30 ppm."),
        ("yan", "250 degF", "Use ppm or mg/L for YAN, e.g. 250 ppm."),
        ("ta", "6.5", "Units are required."),
        ("ta", "6.5 ppm", "Use g/L for TA, e.g. 6.5 g/L."),
        ("ph", "3.4 ppm", "pH has no unit, e.g. 3.40."),
    ],
)
def test_the_wrong_unit_for_the_type_is_rejected(shortid, entered, message):
    form = _form(shortid, entered)

    assert form.errors["value"] == [message]
    assert 'aria-invalid="true"' in str(form["value"])


@pytest.mark.django_db
@pytest.mark.parametrize("shortid, entered", [("specific-gravity", "0"), ("ph", "-1"), ("so2", "-5 ppm")])
def test_zero_or_negative_is_rejected_except_for_temperature(shortid, entered):
    assert "value" in _form(shortid, entered).errors


@pytest.mark.django_db
def test_text_is_rejected():
    assert _form("ph", "high").errors["value"] == ["Enter a number, e.g. 3.40."]


def test_reading_field_is_a_measurement_field_without_its_own_unit_rules():
    field = ReadingField()

    assert str(field.clean("68 F").units) == "degree_Fahrenheit"
    assert str(field.clean("20 C").units) == "degree_Celsius"
    assert str(field.clean("1.050").units) == "dimensionless"


# ---------- Brix for Specific Gravity ----------

@pytest.mark.django_db
@pytest.mark.parametrize("entered", ["12", "12 bx", "12 Brix", "12 brix", "12 °Bx"])
def test_brix_is_converted_to_sg_corrected_for_alcohol(entered):
    batch = BatchFactory()  # starting gravity 1.090
    expected, _abv = Utils.refractometerCorrection(startSG=batch.startingGravity.magnitude, currentBrix=12)

    form = _form("specific-gravity", entered, batch=batch)

    assert form.is_valid(), form.errors
    value = form.cleaned_data["value"]
    assert str(value.units) == "SpecificGravity"
    assert value.magnitude == pytest.approx(expected)


@pytest.mark.django_db
@pytest.mark.parametrize("entered, is_brix", [("1.199", False), ("1.2", True), ("1.050", False), ("1.050 sg", False)])
def test_only_a_bare_number_above_1_199_is_taken_as_brix(entered, is_brix):
    form = _form("specific-gravity", entered, batch=BatchFactory())

    assert form.is_valid(), form.errors
    assert ("°Bx" in form.cleaned_data["description"]) is is_brix


@pytest.mark.django_db
def test_brix_entry_is_noted_in_the_description():
    form = _form("specific-gravity", "12", batch=BatchFactory(), description="Morning check")

    assert form.is_valid(), form.errors
    assert form.cleaned_data["description"] == "Morning check - from 12 °Bx (refractometer, alcohol-corrected)"


@pytest.mark.django_db
def test_add_test_page_converts_brix_on_save():
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    batch = BatchFactory()

    client.post(reverse("addDetailTest", kwargs={"pk": batch.pk}), {
        "datetime": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
        "type": _type("specific-gravity").pk, "value": "12", "description": "",
    })

    reading = batch.tests.filter(type__shortid="specific-gravity").exclude(description__startswith="Auto").get()
    expected, _abv = Utils.refractometerCorrection(startSG=batch.startingGravity.magnitude, currentBrix=12)
    assert reading.chart_value == pytest.approx(expected)
    assert reading.description == "From 12 °Bx (refractometer, alcohol-corrected)"


# ---------- Model ----------

def test_value_is_a_descriptive_quantity_and_units_is_gone():
    assert isinstance(BatchTest._meta.get_field("value"), DescriptiveQuantityField)
    assert "units" not in {f.name for f in BatchTest._meta.get_fields()}


@pytest.mark.django_db
def test_sg_is_never_converted_to_brix():
    reading = _reading(BatchFactory(), "specific-gravity", "1.050 sg")

    reading.refresh_from_db()

    assert str(reading.value.units) == "SpecificGravity"
    assert reading.value.magnitude == pytest.approx(1.050)


@pytest.mark.django_db
def test_a_legacy_bare_gravity_reading_still_charts_as_sg():
    reading = _reading(BatchFactory(), "specific-gravity", "1.090")

    reading.refresh_from_db()

    assert reading.chart_value == pytest.approx(1.090)
    assert reading.display_value == "1.090"


@pytest.mark.django_db
def test_new_batches_get_an_automatic_gravity_reading_in_sg():
    batch = BatchFactory()

    reading = batch.tests.get(type__shortid="specific-gravity")

    assert reading.chart_value == pytest.approx(batch.startingGravity.magnitude)
    assert batch.current_gravity() == pytest.approx(batch.startingGravity.magnitude)


@pytest.mark.django_db
def test_activity_log_shows_the_reading_as_displayed():
    batch = BatchFactory()

    _reading(batch, "temperature", "68 degF")  # F/C shorthand is a form feature
    _reading(batch, "specific-gravity", "1.020")

    texts = list(batch.activity.values_list("text", flat=True))
    assert "Added [Temperature] :: 68 °F" in texts
    assert "Added [Specific Gravity] :: 1.020" in texts


# ---------- Charts and fault rules use chart values ----------

@pytest.mark.django_db
def test_so2_rule_compares_mg_per_liter_as_ppm():
    batch = BatchFactory()
    _reading(batch, "so2", "8 mg/L")

    flags = get_active_flags([batch])

    assert any("SO" in f["message"] and "8 ppm" in f["message"] for f in flags)


@pytest.mark.django_db
def test_so2_above_the_floor_raises_no_flag():
    batch = BatchFactory()
    _reading(batch, "so2", "30 ppm")

    assert not [f for f in get_active_flags([batch]) if "SO" in f["message"]]


@pytest.mark.django_db
def test_charts_get_chart_values():
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    batch = BatchFactory()
    _reading(batch, "specific-gravity", "1.040", hours_ago=0)  # newer than the automatic starting reading
    _reading(batch, "so2", "25 mg/L", hours_ago=2)

    response = client.get(reverse("batch", kwargs={"pk": batch.pk}))

    assert response.context["gravityChart"]["values"][-1] == pytest.approx(1.040)
    assert response.context["so2Chart"]["values"] == [pytest.approx(25)]
    assert response.context["currentGravity"] == pytest.approx(1.040)


# ---------- Add test page ----------

@pytest.mark.django_db
def test_add_test_form_has_no_units_field_and_gives_placeholders_per_type():
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    batch = BatchFactory()

    response = client.get(reverse("addDetailTest", kwargs={"pk": batch.pk}))

    form = response.context["form"]
    assert list(form.fields) == ["datetime", "type", "value", "description"]
    assert isinstance(form.fields["value"], ReadingField)
    page = response.content.decode()
    assert "data-reading-examples" in page and "1.050 sg" in page and "68 °F" in page


@pytest.mark.django_db
def test_add_test_saves_a_temperature_reading():
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    batch = BatchFactory()

    response = client.post(reverse("addDetailTest", kwargs={"pk": batch.pk}), {
        "datetime": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
        "type": _type("temperature").pk, "value": "68 F", "description": "",
    })

    assert response.status_code == 302
    assert batch.tests.get(type__shortid="temperature").display_value == "68 °F"
