"""
UI item 1 (Aaron, 2026-10-07): amounts read the same everywhere - "15 lb", "5 g",
"6 gal", "20 L", "14 days", "Pitch + 24 h" - on pages, in the API and in edit boxes,
instead of raw stored values ("15.00 pound", "0.02 kilogram", "6.00 gallon", "None").
"""
import html as html_lib
import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from pint import Quantity

from ..factories import (
    AdjunctFactory,
    AdjunctUsageFactory,
    BatchFactory,
    FermentableFactory,
    FermenterFactory,
    RecipeFactory,
    VesselFactory,
    YeastFactory,
)
from ..models import RecipeAdjunct, RecipeFermentable, RecipeYeasts, Vessel


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


@pytest.fixture
def recipe(db):
    recipe = RecipeFactory(batchSize=Quantity(6, "gallons"), with_plan=True)
    use = AdjunctUsageFactory(name="Primary")
    lines = [
        RecipeFermentable.objects.create(fermentable=FermentableFactory(name="Clover Honey"), intended_use=use,
                                         amount=Quantity(15, "lb")),
        RecipeFermentable.objects.create(fermentable=FermentableFactory(name="Imported Fruit"), intended_use=use,
                                         amount=None),
        RecipeAdjunct.objects.create(adjunct=AdjunctFactory(name="Fermaid O"), intended_use=use,
                                     amount=Quantity(0.005, "kilogram"), time_to_add=Quantity(1, "day")),
        RecipeYeasts.objects.create(yeast=YeastFactory(name="Lalvin 71B"), amount=Quantity(0.005, "kilogram")),
    ]
    for line in lines:
        line.recipe.add(recipe)
    return recipe


# The raw stored format this replaces: "15.00 pound", "0.02 kilogram", "6.00 gallon", "14.00 day".
RAW = re.compile(r"\b\d+\.\d+ (gallon|liter|pound|kilogram|gram|ounce|milliliter|day|hour)s?\b")


def _text(response):
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", response.content.decode())).split())


def _input(response, name):
    return html_lib.unescape(re.search(rf'name="{name}"[^>]*value="([^"]*)"', response.content.decode()).group(1))


@pytest.mark.django_db
def test_recipe_page_amounts_read_cleanly(client, recipe):
    text = _text(client.get(reverse("recipe", kwargs={"pk": recipe.pk})))

    assert "Batch size 6 gal" in text
    assert "Clover Honey" in text and "15 lb" in text
    assert "Imported Fruit" in text and "None" not in text
    assert "5 g Pitch + 24 h" in text
    assert not RAW.search(text), RAW.search(text)


@pytest.mark.django_db
def test_recipe_editor_tables_read_cleanly(client, recipe):
    text = _text(client.get(reverse("editRecipe", kwargs={"pk": recipe.pk})))

    assert "15 lb" in text and "Pitch + 24 h" in text
    assert not RAW.search(text), RAW.search(text)


@pytest.mark.django_db
def test_vessel_batch_and_dashboard_volumes_read_cleanly(client):
    vessel = VesselFactory(name="Carboy 9", capacity="6 gallons", fill="5 gallons", status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(name="Clover 9", size=Quantity("20 liters"), fermenter=FermenterFactory(vessel=vessel),
                         vessel=vessel)

    vessel_text = _text(client.get(reverse("vessel", kwargs={"pk": vessel.pk})))
    batch_text = _text(client.get(reverse("batch", kwargs={"pk": batch.pk})))
    dashboard = _text(client.get(reverse("index")))

    assert "Capacity 6 gal" in vessel_text and "Fill 5 gal" in vessel_text and "20 L" in vessel_text
    assert "Capacity 6 gal" in batch_text and "Fill 5 gal" in batch_text
    assert "20 L" in dashboard
    for text in (vessel_text, batch_text, dashboard):
        assert not RAW.search(text), RAW.search(text)


@pytest.mark.django_db
def test_batch_list_api_size(client):
    BatchFactory(size=Quantity("6 gallons"))

    assert client.get(reverse("batch-list")).json()[0]["size"] == "6 gal"


@pytest.mark.django_db
def test_edit_boxes_show_readable_values(client, recipe):
    vessel = VesselFactory(capacity="20 liters", status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(size=Quantity("6 gallons"), recipe=recipe, fermenter=FermenterFactory(vessel=vessel),
                         vessel=vessel)

    assert _input(client.get(reverse("editBatch", kwargs={"pk": batch.pk})), "size") == "6 gal"
    assert _input(client.get(reverse("editVessel", kwargs={"pk": vessel.pk})), "capacity") == "20 L"
    assert _input(client.get(reverse("editRecipe", kwargs={"pk": recipe.pk})), "batchSize") == "6 gal"
    assert _input(client.get(reverse("editRecipePlan", kwargs={"pk": recipe.pk})), "steps-0-planned_duration") == "14 days"


@pytest.mark.django_db
def test_an_unchanged_readable_value_re_saves_to_the_same_amount(client, recipe):
    vessel = VesselFactory(status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(size=Quantity("22.7125 liters"), recipe=recipe, fermenter=FermenterFactory(vessel=vessel),
                         vessel=vessel)
    shown = _input(client.get(reverse("editBatch", kwargs={"pk": batch.pk})), "size")

    from ..lib.display import as_quantity
    assert as_quantity(shown).to("liter").magnitude == pytest.approx(22.7125, rel=1e-5)
