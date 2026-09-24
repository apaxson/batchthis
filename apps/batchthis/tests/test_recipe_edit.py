"""Saving the recipe form (New recipe / Edit recipe) returns to the recipe's page."""
import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from ..factories import BatchCategoryFactory, RecipeFactory
from ..models import Recipe


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


def _data(category, **overrides):
    data = {"name": "Orange blossom", "dateCreated": "2026-09-24", "style": category.style.pk,
            "category": category.pk, "brewer": "Aaron", "batchSize": "6 gallons", "notes": "",
            "estOG": "1.100", "estFG": "1.010", "estABV": "11.8"}
    data.update(overrides)
    return data


@pytest.mark.django_db
def test_saving_an_edited_recipe_returns_to_its_page(client):
    recipe = RecipeFactory(name="Old name")
    category = BatchCategoryFactory()

    response = client.post(reverse("editRecipe", kwargs={"pk": recipe.pk}), _data(category, name="New name"))

    assert response.status_code == 302
    assert response["Location"] == reverse("recipe", kwargs={"pk": recipe.pk})
    recipe.refresh_from_db()
    assert recipe.name == "New name" and Recipe.objects.count() == 1


@pytest.mark.django_db
def test_saving_a_new_recipe_goes_to_its_page(client):
    response = client.post(reverse("addRecipe"), _data(BatchCategoryFactory()))

    recipe = Recipe.objects.get(name="Orange blossom")
    assert response.status_code == 302
    assert response["Location"] == reverse("recipe", kwargs={"pk": recipe.pk})


@pytest.mark.django_db
def test_an_edit_with_errors_stays_on_the_edit_form_for_that_recipe(client):
    recipe = RecipeFactory(name="Old name")

    response = client.post(reverse("editRecipe", kwargs={"pk": recipe.pk}), _data(BatchCategoryFactory(), name=""))

    page = response.content.decode()
    assert response.status_code == 200
    assert "Edit recipe" in page
    assert reverse("recipe", kwargs={"pk": recipe.pk}) in page   # "Back to recipe"
    recipe.refresh_from_db()
    assert recipe.name == "Old name"
