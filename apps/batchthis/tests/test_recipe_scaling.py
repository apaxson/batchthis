"""
RECIPE SCALING (TODO.txt; Aaron 2026-10-03): a batch keeps its own copy of the
recipe's ingredients, scaled linearly to the batch size when the batch is made, so
its amounts are accurate for that batch. Add batch previews the scaled list.
"""
import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from pint import Quantity

from ..factories import (
    AdjunctFactory,
    AdjunctUsageFactory,
    FermentableFactory,
    FermenterFactory,
    RecipeFactory,
    VesselFactory,
    YeastFactory,
)
from ..models import (
    Batch,
    BatchIngredient,
    RecipeAdjunct,
    RecipeFermentable,
    RecipeYeasts,
    Vessel,
)
from ..services import recipe_scale_factor, save_batch_edit, scaled_recipe_ingredients


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


@pytest.fixture
def recipe(db):
    """A 6 gallon recipe: 12 lb honey, 5 g Fermaid O at Pitch + 24 h, 5 g 71B, and an imported fermentable with no amount."""
    recipe = RecipeFactory(batchSize=Quantity(6, "gallons"), with_plan=True)
    use = AdjunctUsageFactory(name="Primary")
    honey = RecipeFermentable.objects.create(
        fermentable=FermentableFactory(name="Orange Blossom Honey"), intended_use=use, amount=Quantity(12, "lb"),
        recipe_notes="Add in two parts",
    )
    unmeasured = RecipeFermentable.objects.create(
        fermentable=FermentableFactory(name="Imported Fruit"), intended_use=use, amount=None,
    )
    nutrient = RecipeAdjunct.objects.create(
        adjunct=AdjunctFactory(name="Fermaid O"), intended_use=use, amount=Quantity(5, "gram"),
        time_to_add=Quantity(24, "hour"), recipe_notes="TOSNA 1 of 4",
    )
    yeast = RecipeYeasts.objects.create(yeast=YeastFactory(name="Lalvin 71B"), amount=Quantity(0.005, "kilograms"))
    for item in (honey, unmeasured, nutrient, yeast):
        item.recipe.add(recipe)
    return recipe


def _amounts(items):
    return {item.name: (None if item.amount is None else round(item.amount.to(item.recipe_amount.units).magnitude, 6))
            for item in items}


# ---------- The scale factor ----------

@pytest.mark.django_db
@pytest.mark.parametrize("size, factor", [("12 gallons", 2.0), ("3 gallons", 0.5), ("22.712 liters", 1.0)])
def test_scale_factor_is_batch_size_over_recipe_size(recipe, size, factor):
    assert recipe_scale_factor(recipe, Quantity(size)) == pytest.approx(factor, rel=1e-4)


@pytest.mark.django_db
def test_a_recipe_without_a_size_has_no_scale_factor():
    assert recipe_scale_factor(RecipeFactory(batchSize=Quantity(0, "gallons")), Quantity("6 gallons")) is None


# ---------- Scaling the ingredient list ----------

@pytest.mark.django_db
def test_every_amount_scales_linearly_and_keeps_its_units(recipe):
    items = scaled_recipe_ingredients(recipe, Quantity("12 gallons"))

    assert _amounts(items) == {"Orange Blossom Honey": 24, "Imported Fruit": None, "Fermaid O": 10, "Lalvin 71B": 0.01}
    honey = next(i for i in items if i.name == "Orange Blossom Honey")
    assert str(honey.amount.units) == "pound" and honey.kind == BatchIngredient.KIND_FERMENTABLE


@pytest.mark.django_db
def test_scaled_items_keep_their_timing_use_and_notes(recipe):
    nutrient = next(i for i in scaled_recipe_ingredients(recipe, Quantity("12 gallons")) if i.name == "Fermaid O")

    assert nutrient.time_to_add.to("hour").magnitude == pytest.approx(24)
    assert nutrient.intended_use.name == "Primary" and nutrient.notes == "TOSNA 1 of 4"
    assert nutrient.recipe_amount.to("gram").magnitude == pytest.approx(5)


@pytest.mark.django_db
def test_a_recipe_without_a_size_is_copied_unscaled():
    recipe = RecipeFactory(batchSize=Quantity(0, "gallons"))
    honey = RecipeFermentable.objects.create(fermentable=FermentableFactory(name="Honey"),
                                             intended_use=AdjunctUsageFactory(), amount=Quantity(3, "lb"))
    honey.recipe.add(recipe)

    (item,) = scaled_recipe_ingredients(recipe, Quantity("12 gallons"))

    assert item.amount.to("lb").magnitude == pytest.approx(3)


# ---------- The batch's own copy ----------

def _add_batch_data(recipe, **overrides):
    fermenter = FermenterFactory(vessel=VesselFactory(status=Vessel.STATUS_READY))
    data = {"name": "Orange blossom 1", "startdate": timezone.localdate().isoformat(), "size": "12 gallons",
            "fermenter": fermenter.pk, "startingGravity": "1.100", "estimatedEndGravity": "1.010",
            "recipe": recipe.pk, "workflow_template": ""}
    return data | overrides


@pytest.mark.django_db
def test_add_batch_copies_the_scaled_ingredients(client, recipe):
    response = client.post(reverse("addBatch"), _add_batch_data(recipe))

    batch = Batch.objects.get(name="Orange blossom 1")
    assert response.status_code == 302
    assert _amounts(batch.ingredients.all()) == {"Orange Blossom Honey": 24, "Imported Fruit": None,
                                                 "Fermaid O": 10, "Lalvin 71B": 0.01}


@pytest.mark.django_db
def test_later_recipe_edits_dont_change_the_batch(client, recipe):
    client.post(reverse("addBatch"), _add_batch_data(recipe))
    RecipeFermentable.objects.filter(fermentable__name="Orange Blossom Honey").update(amount=Quantity(20, "lb"))

    batch = Batch.objects.get(name="Orange blossom 1")
    assert _amounts(batch.ingredients.all())["Orange Blossom Honey"] == 24


@pytest.mark.django_db
def test_correcting_the_batch_size_rescales_its_ingredients(client, recipe):
    client.post(reverse("addBatch"), _add_batch_data(recipe))
    batch = Batch.objects.get(name="Orange blossom 1")

    save_batch_edit(batch, name=batch.name, recipe=recipe, size=Quantity("3 gallons"),
                    starting_gravity=1.100, estimated_end_gravity=1.010)

    assert _amounts(batch.ingredients.all())["Orange Blossom Honey"] == 6


@pytest.mark.django_db
def test_an_edit_that_keeps_size_and_recipe_leaves_the_ingredients_alone(client, recipe):
    client.post(reverse("addBatch"), _add_batch_data(recipe))
    batch = Batch.objects.get(name="Orange blossom 1")
    before = list(batch.ingredients.values_list("pk", flat=True))

    save_batch_edit(batch, name="Renamed", recipe=recipe, size=Quantity("12 gallons"),
                    starting_gravity=1.100, estimated_end_gravity=1.010)

    assert list(batch.ingredients.values_list("pk", flat=True)) == before


# ---------- Add batch preview (API) ----------

def _preview(client, recipe, size):
    return client.get(reverse("recipe-scaled", kwargs={"pk": recipe.pk}), {"size": size})


@pytest.mark.django_db
def test_preview_lists_the_scaled_ingredients(client, recipe):
    response = _preview(client, recipe, "12 gallons")

    assert response.status_code == 200
    data = response.json()
    assert data["factor"] == pytest.approx(2.0)
    assert data["recipe_size"] == "6 gal" and data["batch_size"] == "12 gal"
    rows = {row["name"]: row for row in data["ingredients"]}
    assert rows["Orange Blossom Honey"]["recipe_amount"] == "12 lb"
    assert rows["Orange Blossom Honey"]["amount"] == "24 lb"
    assert rows["Imported Fruit"]["amount"] == ""
    assert rows["Fermaid O"]["time_to_add"] == "Pitch + 24 h"


@pytest.mark.django_db
@pytest.mark.parametrize("size", ["", "12", "12 lb", "-3 gallons"])
def test_preview_rejects_a_size_that_isnt_a_volume(client, recipe, size):
    response = _preview(client, recipe, size)

    assert response.status_code == 400
    assert response.json()["error"]


@pytest.mark.django_db
def test_preview_requires_login(recipe):
    assert _preview(Client(), recipe, "12 gallons").status_code in (401, 403)


@pytest.mark.django_db
def test_add_batch_page_has_the_ingredient_preview(client):
    html = client.get(reverse("addBatch")).content.decode()

    assert 'id="scale-preview"' in html
    assert f'data-scaled-url-base="{reverse("recipe-list")}"' in html


@pytest.mark.parametrize("value, label", [
    (Quantity(0.005814, "kilogram"), "5.814 g"), (Quantity(0.25, "liter"), "250 ml"),
    (Quantity(24, "lb"), "24 lb"), (Quantity(10, "liter"), "10 L"), (Quantity(2.5, "kilogram"), "2.5 kg"), (None, ""),
])
def test_amount_labels_show_small_metric_amounts_in_g_and_ml(value, label):
    from ..services import quantity_label

    assert quantity_label(value) == label


def test_a_bare_number_is_shown_without_failing():
    from ..services import quantity_label

    assert quantity_label(0) == "0"
