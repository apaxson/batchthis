"""
Food pairings on recipes (Aaron, 2026-09-25): shared PairingTag rows (a name,
unique ignoring case, kept even when no recipe uses it) linked many-to-many from
Recipe.pairings - replacing the unused free-text Recipe.pairing. The recipe form
takes them as a comma-separated list (forms.PairingTagsField), which the
react-select tag picker (TagSelect.jsx) fills in; services.set_recipe_pairings()
saves them, reusing existing tags.
"""
import pytest
from django import forms
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

from ..factories import BatchCategoryFactory, RecipeFactory
from ..forms import PairingTagsField
from ..models import PairingTag, Recipe
from ..services import set_recipe_pairings


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


def _data(category, **overrides):
    data = {"name": "Orange blossom", "dateCreated": "2026-09-24", "style": category.style.pk,
            "category": category.pk, "brewer": "Aaron", "batchSize": "6 gallons", "notes": "",
            "estOG": "1.100", "estFG": "1.010", "estABV": "11.8", "pairings": ""}
    data.update(overrides)
    return data


def _names(recipe):
    return sorted(recipe.pairings.values_list("name", flat=True))


# ---------- Model ----------

def test_recipe_has_a_pairings_link_instead_of_the_old_text_field():
    names = {f.name for f in Recipe._meta.get_fields()}

    assert "pairing" not in names
    field = Recipe._meta.get_field("pairings")
    assert field.many_to_many and field.related_model is PairingTag and field.blank


@pytest.mark.django_db
def test_tag_names_are_unique_ignoring_case():
    PairingTag.objects.create(name="Aged cheddar")

    with pytest.raises(IntegrityError), transaction.atomic():
        PairingTag.objects.create(name="aged CHEDDAR")


@pytest.mark.django_db
def test_tags_list_in_alphabetical_order_ignoring_case():
    for name in ("roast chicken", "Aged cheddar", "Blue cheese"):
        PairingTag.objects.create(name=name)

    assert [t.name for t in PairingTag.objects.all()] == ["Aged cheddar", "Blue cheese", "roast chicken"]


# ---------- Form field ----------

class _Form(forms.Form):
    pairings = PairingTagsField(required=False)


def test_the_field_splits_trims_and_drops_duplicates_ignoring_case():
    form = _Form(data={"pairings": "Roast chicken,  aged cheddar , , Roast Chicken"})

    assert form.is_valid()
    assert form.cleaned_data["pairings"] == ["Roast chicken", "aged cheddar"]


def test_an_empty_field_is_no_pairings():
    form = _Form(data={"pairings": " , "})

    assert form.is_valid()
    assert form.cleaned_data["pairings"] == []


def test_a_name_that_is_too_long_is_rejected():
    long_name = "x" * (PairingTag.NAME_MAX_LENGTH + 1)

    form = _Form(data={"pairings": f"Cheese, {long_name}"})

    assert not form.is_valid()
    assert form.errors["pairings"] == [
        f"Keep each pairing to {PairingTag.NAME_MAX_LENGTH} characters or fewer: '{long_name}'."
    ]


@pytest.mark.django_db
def test_the_field_shows_saved_tags_as_a_comma_list():
    tags = [PairingTag.objects.create(name="Roast chicken"), PairingTag.objects.create(name="Aged cheddar")]

    assert PairingTagsField().prepare_value(tags) == "Roast chicken, Aged cheddar"


# ---------- Service ----------

@pytest.mark.django_db
def test_setting_pairings_creates_new_tags_and_reuses_existing_ones_ignoring_case():
    existing = PairingTag.objects.create(name="Aged Cheddar")
    recipe = RecipeFactory()

    set_recipe_pairings(recipe, ["aged cheddar", "Fruit tart"])

    assert _names(recipe) == ["Aged Cheddar", "Fruit tart"]
    assert recipe.pairings.get(name="Aged Cheddar") == existing
    assert PairingTag.objects.count() == 2


@pytest.mark.django_db
def test_removing_a_pairing_keeps_the_tag_for_other_recipes():
    recipe = RecipeFactory()
    set_recipe_pairings(recipe, ["Roast chicken", "Fruit tart"])

    set_recipe_pairings(recipe, ["Roast chicken"])

    assert _names(recipe) == ["Roast chicken"]
    assert PairingTag.objects.filter(name="Fruit tart").exists()


@pytest.mark.django_db
def test_setting_no_pairings_clears_them():
    recipe = RecipeFactory()
    set_recipe_pairings(recipe, ["Roast chicken"])

    set_recipe_pairings(recipe, [])

    assert _names(recipe) == []


# ---------- Add / Edit recipe ----------

@pytest.mark.django_db
def test_a_new_recipe_saves_its_pairings(client):
    PairingTag.objects.create(name="Aged cheddar")

    response = client.post(reverse("addRecipe"), _data(BatchCategoryFactory(), pairings="Roast chicken, aged cheddar"))

    assert response.status_code == 302
    assert _names(Recipe.objects.get(name="Orange blossom")) == ["Aged cheddar", "Roast chicken"]


@pytest.mark.django_db
def test_the_edit_page_shows_the_pairings_and_the_tag_picker(client):
    recipe = RecipeFactory()
    set_recipe_pairings(recipe, ["Roast chicken", "Aged cheddar"])

    page = client.get(reverse("editRecipe", kwargs={"pk": recipe.pk})).content.decode()

    assert 'name="pairings"' in page
    assert 'value="Aged cheddar, Roast chicken"' in page
    assert f'data-endpoint="{reverse("pairing-list")}"' in page
    assert 'data-target="#id_pairings"' in page
    assert "batchthis/dist/tag-select.js" in page


@pytest.mark.django_db
def test_an_edit_replaces_the_pairings(client):
    recipe = RecipeFactory()
    set_recipe_pairings(recipe, ["Roast chicken"])
    category = BatchCategoryFactory()

    client.post(reverse("editRecipe", kwargs={"pk": recipe.pk}), _data(category, pairings="Fruit tart, Blue cheese"))

    assert _names(recipe) == ["Blue cheese", "Fruit tart"]


@pytest.mark.django_db
def test_an_edit_with_errors_keeps_the_typed_pairings_and_saves_nothing(client):
    recipe = RecipeFactory()
    set_recipe_pairings(recipe, ["Roast chicken"])

    response = client.post(reverse("editRecipe", kwargs={"pk": recipe.pk}),
                           _data(BatchCategoryFactory(), name="", pairings="Fruit tart"))

    assert response.status_code == 200
    assert 'value="Fruit tart"' in response.content.decode()
    assert _names(recipe) == ["Roast chicken"]


# ---------- API ----------

@pytest.mark.django_db
def test_the_api_lists_every_tag_alphabetically():
    api = APIClient()
    api.force_authenticate(user=get_user_model().objects.create_user(username="taster", password="pw"))
    fruit = PairingTag.objects.create(name="fruit tart")
    cheddar = PairingTag.objects.create(name="Aged cheddar")

    response = api.get(reverse("pairing-list"))

    assert response.status_code == 200
    assert response.json() == [{"id": cheddar.id, "name": "Aged cheddar"}, {"id": fruit.id, "name": "fruit tart"}]


@pytest.mark.django_db
def test_the_api_requires_login():
    assert APIClient().get(reverse("pairing-list")).status_code == 403


# ---------- Recipe page ----------

@pytest.mark.django_db
def test_the_recipe_page_shows_the_pairings_as_tags(client):
    recipe = RecipeFactory()
    set_recipe_pairings(recipe, ["Roast chicken", "Aged cheddar"])

    page = client.get(reverse("recipe", kwargs={"pk": recipe.pk})).content.decode()

    assert '<span class="cl-tag">Aged cheddar</span>' in page
    assert '<span class="cl-tag">Roast chicken</span>' in page


@pytest.mark.django_db
def test_the_recipe_page_says_when_there_are_no_pairings(client):
    recipe = RecipeFactory()

    page = client.get(reverse("recipe", kwargs={"pk": recipe.pk})).content.decode()

    assert "Food pairing" in page and "No pairings yet." in page


# ---------- Admin ----------

def test_pairing_tags_are_view_only_in_the_admin():
    model_admin = admin.site._registry[PairingTag]

    assert not model_admin.has_add_permission(None)
    assert not model_admin.has_change_permission(None)
    assert not model_admin.has_delete_permission(None)
