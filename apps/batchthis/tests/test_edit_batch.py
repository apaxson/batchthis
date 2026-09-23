import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from pint import Quantity

from ..factories import BatchFactory, FermenterFactory, RecipeFactory, VesselFactory
from ..models import Batch, Vessel


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


def _url(batch):
    return reverse("editBatch", kwargs={"pk": batch.pk})


def _in_use_batch(**kwargs):
    vessel = VesselFactory(name="Carboy 1", status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(fermenter=FermenterFactory(vessel=vessel), recipe=RecipeFactory(), **kwargs)
    batch.vessel = vessel
    batch.save()
    return batch


def _data(batch, **overrides):
    data = {
        "name": batch.name,
        "recipe": batch.recipe.pk,
        "size": str(batch.size),
        "startingGravity": f"{batch.startingGravity.magnitude:.3f}",
        "estimatedEndGravity": f"{batch.estimatedEndGravity.magnitude:.3f}",
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
def test_edit_updates_the_batch_in_place_without_creating_a_duplicate(client):
    batch = _in_use_batch(name="Spring Mead")
    new_recipe = RecipeFactory()

    response = client.post(
        _url(batch),
        _data(batch, name="Spring Mead v2", recipe=new_recipe.pk, size="5 gallons",
              startingGravity="1.100", estimatedEndGravity="0.998"),
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("batch", kwargs={"pk": batch.pk})
    assert Batch.objects.count() == 1
    batch.refresh_from_db()
    assert batch.name == "Spring Mead v2"
    assert batch.recipe == new_recipe
    assert batch.size.magnitude == pytest.approx(5)
    assert batch.startingGravity.magnitude == pytest.approx(1.100)
    assert batch.estimatedEndGravity.magnitude == pytest.approx(0.998)


@pytest.mark.django_db
def test_edit_never_touches_the_vessel_or_its_status(client):
    batch = _in_use_batch()
    vessel = batch.vessel
    other = FermenterFactory(vessel=VesselFactory(status=Vessel.STATUS_READY))

    # Even if a fermenter/vessel is posted, the edit form doesn't accept it.
    client.post(_url(batch), _data(batch, name="Renamed", fermenter=other.pk, vessel=other.vessel.pk))

    batch.refresh_from_db()
    vessel.refresh_from_db()
    other.vessel.refresh_from_db()
    assert batch.fermenter.vessel == vessel
    assert batch.vessel == vessel
    assert vessel.status == Vessel.STATUS_ACTIVE
    assert other.vessel.status == Vessel.STATUS_READY
    assert not vessel.status_events.exists()
    assert not other.vessel.status_events.exists()


@pytest.mark.django_db
def test_edit_never_changes_the_start_date(client):
    batch = _in_use_batch()
    started = batch.startdate

    client.post(_url(batch), _data(batch, name="Renamed", startdate="2020-01-01"))

    batch.refresh_from_db()
    assert batch.startdate == started


@pytest.mark.django_db
def test_edit_form_is_prefilled_with_the_batch_and_shows_vessel_read_only(client):
    batch = _in_use_batch(name="Spring Mead", startingGravity=Quantity(1.105, "sg"))

    response = client.get(_url(batch))

    assert response.status_code == 200
    form = response.context["form"]
    assert "fermenter" not in form.fields
    assert "startdate" not in form.fields
    html = response.content.decode()
    assert 'value="Spring Mead"' in html
    assert 'value="1.105"' in html
    assert "Carboy 1" in html  # current vessel shown, read-only


@pytest.mark.django_db
def test_invalid_edit_shows_errors_and_changes_nothing(client):
    batch = _in_use_batch(name="Spring Mead")

    response = client.post(_url(batch), _data(batch, name="", startingGravity="not a number"))

    assert response.status_code == 200
    form = response.context["form"]
    assert "name" in form.errors
    assert "startingGravity" in form.errors
    batch.refresh_from_db()
    assert batch.name == "Spring Mead"


@pytest.mark.django_db
def test_an_edit_logs_one_batch_modified_activity_entry(client):
    batch = _in_use_batch()
    before = batch.activity.count()

    client.post(_url(batch), _data(batch, name="Renamed"))

    assert batch.activity.count() == before + 1
    assert batch.activity.order_by("-pk").first().text == "Batch Modified"


@pytest.mark.django_db
def test_saving_with_no_changes_does_not_touch_the_batch(client):
    batch = _in_use_batch()
    before = batch.activity.count()

    response = client.post(_url(batch), _data(batch))

    assert response.status_code == 302
    assert batch.activity.count() == before


@pytest.mark.django_db
def test_edit_404s_for_a_missing_batch(client):
    assert client.get(reverse("editBatch", kwargs={"pk": 9999})).status_code == 404


@pytest.mark.django_db
def test_edit_requires_login():
    batch = _in_use_batch(name="Spring Mead")

    response = Client().post(_url(batch), {"name": "Hacked"})

    assert response.status_code == 302
    assert "login" in response["Location"]
    batch.refresh_from_db()
    assert batch.name == "Spring Mead"


@pytest.mark.django_db
def test_add_batch_page_no_longer_serves_edits(client):
    # /addBatch is create-only again; the edit URL has its own view.
    assert reverse("editBatch", kwargs={"pk": 1}) != reverse("addBatch")
    response = client.get(reverse("addBatch"))
    assert response.status_code == 200
    assert "New batch" in response.content.decode()
