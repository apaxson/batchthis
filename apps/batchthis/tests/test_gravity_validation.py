"""
Estimated end gravity must be lower than the starting gravity (Aaron, 2026-09-25).
Equal gravities made Batch.percent_complete() divide by zero, so the batch page
crashed; a higher end gravity gave a meaningless "% to estimated FG". Add batch
and Edit batch both check it (forms.GravityPairMixin), and the page shows "—"
instead of crashing for a batch saved before the check.
"""
import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from pint import Quantity

from ..factories import BatchFactory, FermenterFactory, RecipeFactory, VesselFactory
from ..models import Batch, Vessel

FG_ERROR = "Estimated end gravity must be lower than the starting gravity (1.050)."


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


def _batch(**kwargs):
    vessel = VesselFactory(name="Carboy 1", status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(fermenter=FermenterFactory(vessel=vessel), recipe=RecipeFactory(), **kwargs)
    batch.vessel = vessel
    batch.save()
    return batch


def _edit(client, batch, sg, fg):
    return client.post(reverse("editBatch", kwargs={"pk": batch.pk}), {
        "name": batch.name, "recipe": batch.recipe.pk, "size": str(batch.size),
        "startingGravity": sg, "estimatedEndGravity": fg,
    })


def _add(client, sg, fg, name="New batch"):
    return client.post(reverse("addBatch"), {
        "name": name, "startdate": "2026-09-17", "size": "6 gallons",
        "fermenter": FermenterFactory().pk, "recipe": RecipeFactory(with_plan=True).pk,
        "startingGravity": sg, "estimatedEndGravity": fg,
    })


# ---------- Edit batch ----------

@pytest.mark.django_db
@pytest.mark.parametrize("fg", ["1.050", "1.060"])
def test_edit_rejects_an_end_gravity_not_below_the_starting_gravity(client, fg):
    batch = _batch()

    response = _edit(client, batch, "1.050", fg)

    assert response.status_code == 200
    assert response.context["form"].errors["estimatedEndGravity"] == [FG_ERROR]
    batch.refresh_from_db()
    assert batch.startingGravity.magnitude == pytest.approx(1.09)
    assert batch.estimatedEndGravity.magnitude == pytest.approx(1.005)


@pytest.mark.django_db
def test_edit_accepts_an_end_gravity_below_the_starting_gravity(client):
    batch = _batch()

    response = _edit(client, batch, "1.050", "0.998")

    assert response.status_code == 302
    batch.refresh_from_db()
    assert batch.estimatedEndGravity.magnitude == pytest.approx(0.998)


# ---------- Add batch ----------

@pytest.mark.django_db
@pytest.mark.parametrize("fg", ["1.050", "1.060"])
def test_add_rejects_an_end_gravity_not_below_the_starting_gravity(client, fg):
    response = _add(client, "1.050", fg)

    assert response.status_code == 200
    assert response.context["form"].errors["estimatedEndGravity"] == [FG_ERROR]
    assert not Batch.objects.filter(name="New batch").exists()


@pytest.mark.django_db
def test_add_rejects_a_gravity_that_is_not_a_number(client):
    # Regression: addBatch ran float() on the raw text and crashed.
    response = _add(client, "abc", "1.000")

    assert response.status_code == 200
    assert response.context["form"].errors["startingGravity"] == ["Enter a specific gravity, e.g. 1.090."]
    assert not Batch.objects.filter(name="New batch").exists()


# ---------- Batch saved before the check ----------

@pytest.mark.django_db
def test_percent_complete_is_none_when_the_end_gravity_is_not_below_the_start():
    batch = _batch(startingGravity=Quantity(1.05, "sg"), estimatedEndGravity=Quantity(1.05, "sg"))

    assert batch.percent_complete() is None


@pytest.mark.django_db
def test_batch_page_shows_a_dash_instead_of_crashing_for_equal_gravities(client):
    batch = _batch(startingGravity=Quantity(1.05, "sg"), estimatedEndGravity=Quantity(1.05, "sg"))

    response = client.get(reverse("batch", kwargs={"pk": batch.pk}))

    assert response.status_code == 200
    assert "&mdash; to estimated FG" in response.content.decode()
