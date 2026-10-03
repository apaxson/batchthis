"""
The batch recipe page (Aaron, 2026-10-03): a batch links to its adjusted "current
recipe" - its own scaled ingredients (BatchIngredient) - which is NOT a Recipe, so it
never shows under recipes/.
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
from ..models import Recipe, RecipeAdjunct, RecipeFermentable, RecipeYeasts, Vessel
from ..services import copy_recipe_ingredients_to_batch


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


@pytest.fixture
def recipe(db):
    recipe = RecipeFactory(name="Orange Blossom Traditional", batchSize=Quantity(6, "gallons"))
    use = AdjunctUsageFactory(name="Primary")
    items = [
        RecipeFermentable.objects.create(fermentable=FermentableFactory(name="Orange Blossom Honey"), intended_use=use,
                                         amount=Quantity(12, "lb"), recipe_notes="Add in two parts"),
        RecipeAdjunct.objects.create(adjunct=AdjunctFactory(name="Fermaid O"), intended_use=use,
                                     amount=Quantity(5, "gram"), time_to_add=Quantity(24, "hour")),
        RecipeYeasts.objects.create(yeast=YeastFactory(name="Lalvin 71B"), amount=Quantity(5, "gram")),
    ]
    for item in items:
        item.recipe.add(recipe)
    return recipe


def _batch(recipe, size="12 gallons", copy=True):
    vessel = VesselFactory(status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(name="Orange blossom 12 gal", recipe=recipe, size=Quantity(size),
                         fermenter=FermenterFactory(vessel=vessel), vessel=vessel)
    if copy:
        copy_recipe_ingredients_to_batch(batch)
    return batch


def _text(response):
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", response.content.decode())).split())


@pytest.mark.django_db
def test_batch_recipe_shows_the_scaled_amounts_next_to_the_recipes(client, recipe):
    batch = _batch(recipe)

    text = _text(client.get(reverse("batchRecipe", kwargs={"pk": batch.pk})))

    assert "Recipe 6 gal → batch 12 gal · ×2" in text
    assert "Orange Blossom Honey" in text and "12 lb 24 lb" in text
    assert "Fermaid O" in text and "5 g 10 g Pitch + 24 h" in text
    assert "Lalvin 71B" in text and "5 g 10 g" in text


@pytest.mark.django_db
def test_batch_recipe_is_not_a_recipe(client, recipe):
    _batch(recipe)

    assert Recipe.objects.count() == 1
    listing = client.get(reverse("recipe-list")).json()
    assert [r["name"] for r in listing] == ["Orange Blossom Traditional"]   # what recipes/ lists


@pytest.mark.django_db
def test_batch_page_links_to_its_batch_recipe(client, recipe):
    batch = _batch(recipe)

    page = client.get(reverse("batch", kwargs={"pk": batch.pk})).content.decode()

    assert f'href="{reverse("batchRecipe", kwargs={"pk": batch.pk})}"' in page


@pytest.mark.django_db
def test_a_batch_made_before_scaling_offers_to_copy_its_recipe(client, recipe):
    batch = _batch(recipe, copy=False)
    url = reverse("batchRecipe", kwargs={"pk": batch.pk})

    page = client.get(url).content.decode()
    assert reverse("copyBatchRecipe", kwargs={"pk": batch.pk}) in page

    response = client.post(reverse("copyBatchRecipe", kwargs={"pk": batch.pk}))

    assert response.status_code == 302 and response["Location"] == url
    assert batch.ingredients.count() == 3


@pytest.mark.django_db
def test_copying_again_doesnt_replace_an_existing_batch_recipe(client, recipe):
    batch = _batch(recipe)
    before = list(batch.ingredients.values_list("pk", flat=True))

    response = client.post(reverse("copyBatchRecipe", kwargs={"pk": batch.pk}), follow=True)

    assert list(batch.ingredients.values_list("pk", flat=True)) == before
    assert "already has its recipe" in response.content.decode()


@pytest.mark.django_db
def test_copy_rejects_get(client, recipe):
    batch = _batch(recipe, copy=False)

    assert client.get(reverse("copyBatchRecipe", kwargs={"pk": batch.pk})).status_code == 405
    assert batch.ingredients.count() == 0


@pytest.mark.django_db
def test_batch_recipe_requires_login(recipe):
    batch = _batch(recipe)

    response = Client().get(reverse("batchRecipe", kwargs={"pk": batch.pk}))

    assert response.status_code == 302 and "login" in response["Location"]
