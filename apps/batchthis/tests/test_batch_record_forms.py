"""Add test / Add note / Add addon on a batch: login, validation, fixed batch, defaults."""
import datetime

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from ..factories import BatchFactory
from ..models import (
    BatchAddition,
    BatchAdditionItem,
    BatchNote,
    BatchNoteType,
    BatchTest,
    BatchTestType,
    Unit,
)


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


@pytest.fixture
def batch(db):
    return BatchFactory()


def _when():
    return timezone.localtime().replace(second=0, microsecond=0) - datetime.timedelta(hours=3)


def _test_data(**overrides):
    data = {
        "datetime": _when().strftime("%Y-%m-%dT%H:%M"),
        "type": BatchTestType.objects.get(shortid="ph").pk,
        "value": "3.45",
        "units": Unit.objects.first().pk,
        "description": "After nutrient",
    }
    data.update(overrides)
    return data


def _note_data(**overrides):
    data = {
        "date": _when().strftime("%Y-%m-%dT%H:%M"),
        "notetype": BatchNoteType.objects.get(name="Tasting Note").pk,
        "text": "Honey forward, a little hot",
    }
    data.update(overrides)
    return data


def _addition_data(**overrides):
    item, _ = BatchAdditionItem.objects.get_or_create(name="Fermaid-O")
    data = {"name": item.pk, "amount": "4", "units": Unit.objects.first().pk, "description": "1/3 sugar break"}
    data.update(overrides)
    return data


PAGES = [
    ("addDetailTest", _test_data, BatchTest, "addTest.html"),
    ("addDetailNote", _note_data, BatchNote, "addNote.html"),
    ("addDetailAddon", _addition_data, BatchAddition, "addAddon.html"),
]


# ---------- Access ----------

@pytest.mark.django_db
@pytest.mark.parametrize("url_name, data, model, template", PAGES)
def test_pages_require_login(batch, url_name, data, model, template):
    url = reverse(url_name, kwargs={"pk": batch.pk})
    before = model.objects.filter(batch=batch).count()

    for response in (Client().get(url), Client().post(url, data())):
        assert response.status_code == 302 and "login" in response["Location"]
    assert model.objects.filter(batch=batch).count() == before


@pytest.mark.django_db
@pytest.mark.parametrize("url_name, data, model, template", PAGES)
def test_pages_404_for_a_missing_batch(client, url_name, data, model, template):
    assert client.get(reverse(url_name, kwargs={"pk": 9999})).status_code == 404


@pytest.mark.parametrize("url_name", ["addTest", "addNote", "addAddon"])
def test_batchless_routes_are_gone(url_name):
    with pytest.raises(NoReverseMatch):
        reverse(url_name)


# ---------- GET ----------

@pytest.mark.django_db
@pytest.mark.parametrize("url_name, data, model, template", PAGES)
def test_form_has_no_batch_field_and_defaults_the_time_to_now(client, batch, url_name, data, model, template):
    response = client.get(reverse(url_name, kwargs={"pk": batch.pk}))

    form = response.context["form"]
    assert "batch" not in form.fields
    assert response.context["batch"] == batch
    time_field = {"addDetailTest": "datetime", "addDetailNote": "date"}.get(url_name)
    if time_field:
        # Django hands the form a naive local time for display.
        initial = form[time_field].value()
        assert abs(initial - timezone.localtime().replace(tzinfo=None)) < datetime.timedelta(minutes=2)


@pytest.mark.django_db
def test_note_type_in_the_url_is_preselected(client, batch):
    url = reverse("addDetailNoteType", kwargs={"pk": batch.pk, "noteType": "Fermentation Note"})

    form = client.get(url).context["form"]

    assert form["notetype"].value() == BatchNoteType.objects.get(name="Fermentation Note").pk
    assert form.fields["notetype"].queryset.count() == BatchNoteType.objects.count()


@pytest.mark.django_db
def test_an_unknown_note_type_in_the_url_falls_back_to_the_normal_choice(client, batch):
    url = reverse("addDetailNoteType", kwargs={"pk": batch.pk, "noteType": "Tasting Notez"})

    response = client.get(url)

    assert response.status_code == 200
    assert response.context["form"]["notetype"].value() is None


# ---------- POST: valid ----------

@pytest.mark.django_db
@pytest.mark.parametrize("url_name, data, model, template", PAGES)
def test_a_valid_entry_is_saved_on_the_batch_and_redirects(client, batch, url_name, data, model, template):
    before = model.objects.filter(batch=batch).count()

    response = client.post(reverse(url_name, kwargs={"pk": batch.pk}), data())

    assert response.status_code == 302
    assert response["Location"] == reverse("batch", kwargs={"pk": batch.pk})
    assert model.objects.filter(batch=batch).count() == before + 1


@pytest.mark.django_db
def test_a_backdated_test_keeps_its_time(client, batch):
    when = _when() - datetime.timedelta(days=2)

    client.post(reverse("addDetailTest", kwargs={"pk": batch.pk}), _test_data(datetime=when.strftime("%Y-%m-%dT%H:%M")))

    assert batch.tests.get(type__shortid="ph").datetime == when


@pytest.mark.django_db
@pytest.mark.parametrize("url_name, data, model, template", PAGES)
def test_a_batch_in_the_post_data_is_ignored(client, batch, url_name, data, model, template):
    other = BatchFactory()
    before_other = model.objects.filter(batch=other).count()

    client.post(reverse(url_name, kwargs={"pk": batch.pk}), data(batch=other.pk))

    assert model.objects.filter(batch=other).count() == before_other
    assert model.objects.filter(batch=batch).exists()


@pytest.mark.django_db
def test_saving_a_note_still_writes_the_activity_log(client, batch):
    client.post(reverse("addDetailNote", kwargs={"pk": batch.pk}), _note_data())

    assert batch.activity.filter(text__contains="Honey forward").exists()


@pytest.mark.django_db
def test_completed_batches_still_accept_notes(client, batch):
    batch.active = False
    batch.save()

    response = client.post(reverse("addDetailNote", kwargs={"pk": batch.pk}), _note_data())

    assert response.status_code == 302
    assert batch.notes.exists()


# ---------- POST: invalid ----------

@pytest.mark.django_db
@pytest.mark.parametrize(
    "url_name, data, model, overrides, field",
    [
        ("addDetailTest", _test_data, BatchTest, {"value": ""}, "value"),
        ("addDetailTest", _test_data, BatchTest, {"value": "high"}, "value"),
        ("addDetailTest", _test_data, BatchTest, {"type": ""}, "type"),
        ("addDetailTest", _test_data, BatchTest, {"datetime": ""}, "datetime"),
        ("addDetailNote", _note_data, BatchNote, {"text": ""}, "text"),
        ("addDetailNote", _note_data, BatchNote, {"notetype": ""}, "notetype"),
        ("addDetailAddon", _addition_data, BatchAddition, {"amount": "a pinch"}, "amount"),
        ("addDetailAddon", _addition_data, BatchAddition, {"name": ""}, "name"),
    ],
)
def test_invalid_input_shows_the_error_and_saves_nothing(client, batch, url_name, data, model, overrides, field):
    before = model.objects.filter(batch=batch).count()

    response = client.post(reverse(url_name, kwargs={"pk": batch.pk}), data(**overrides))

    assert response.status_code == 200
    assert field in response.context["form"].errors
    assert 'aria-invalid="true"' in str(response.context["form"][field])
    assert model.objects.filter(batch=batch).count() == before
