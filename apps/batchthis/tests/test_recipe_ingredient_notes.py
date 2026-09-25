"""
Recipe page: no Notes column in the Fermentables / Adjuncts / Yeast tables - each
row's notes show in the hover tooltip on the ingredient name instead (the recipe's
own note for that line first, then the ingredient's notes).
"""
import html
import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from pint import Quantity

from ..factories import AdjunctFactory, AdjunctUsageFactory, FermentableFactory, RecipeFactory, YeastFactory
from ..models import RecipeAdjunct, RecipeFermentable, RecipeYeasts


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


@pytest.fixture
def recipe(db):
    recipe = RecipeFactory()
    use = AdjunctUsageFactory(name="Primary")
    fermentable = RecipeFermentable.objects.create(
        fermentable=FermentableFactory(name="Orange Blossom Honey", supplier="", description="Light and floral"),
        intended_use=use, amount=Quantity(12, "lb"), recipe_notes="Add in two parts",
    )
    adjunct = RecipeAdjunct.objects.create(
        adjunct=AdjunctFactory(name="Fermaid O", notes="Organic nutrient"),
        intended_use=use, amount=Quantity(5, "gram"), time_to_add=Quantity(0, "min"), recipe_notes="",
    )
    yeast = RecipeYeasts.objects.create(
        yeast=YeastFactory(name="Lalvin 71B", notes="Softens malic acid"),
        amount=Quantity(0.005, "kilograms"), notes="Rehydrate with Go-Ferm",
    )
    fermentable.recipe.add(recipe)
    adjunct.recipe.add(recipe)
    yeast.recipe.add(recipe)
    return recipe


def _page(client, recipe) -> str:
    return html.unescape(client.get(reverse("recipe", kwargs={"pk": recipe.pk})).content.decode())


def _tooltip(page: str, name: str) -> str:
    match = re.search(r'<td[^>]*js-mouse-tooltip[^>]*title="([^"]*)"[^>]*>\s*(?:<span[^>]*>)?' + re.escape(name), page)
    assert match, f"no tooltip cell for {name}"
    return match.group(1)


@pytest.mark.django_db
def test_ingredient_tables_have_no_notes_column(client, recipe):
    page = _page(client, recipe)
    tables = page[page.index("Fermentables"):page.index('<section class="cl-plan"')]

    assert "<th>Notes</th>" not in tables
    assert "Add in two parts</td>" not in tables


@pytest.mark.django_db
@pytest.mark.parametrize("name, expected", [
    ("Orange Blossom Honey", "Add in two parts\nLight and floral"),
    ("Fermaid O", "Organic nutrient"),
    ("Lalvin 71B", "Rehydrate with Go-Ferm\nSoftens malic acid"),
])
def test_ingredient_name_tooltip_shows_the_recipe_note_then_the_ingredient_notes(client, recipe, name, expected):
    assert _tooltip(_page(client, recipe), name) == expected
