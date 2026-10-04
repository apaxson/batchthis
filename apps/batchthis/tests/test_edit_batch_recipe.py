"""
Editing a batch's own recipe (Aaron, 2026-10-03): until the batch is pitched, its
scaled recipe (BatchIngredient rows) can be changed - amounts, timing, notes, items
added or removed. The library Recipe is never changed. After Pitch it's locked.
"""
import datetime
import html

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone
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
from ..models import (
    ActivityLog,
    BatchIngredient,
    BatchStage,
    RecipeAdjunct,
    RecipeFermentable,
    RecipeYeasts,
    Vessel,
)
from ..services import (
    copy_recipe_ingredients_to_batch,
    save_batch_edit,
    transition_stage_event,
)


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


@pytest.fixture
def use(db):
    return AdjunctUsageFactory(name="Primary")


@pytest.fixture
def recipe(use):
    recipe = RecipeFactory(name="Traditional", batchSize=Quantity(6, "gallons"))
    items = [
        RecipeFermentable.objects.create(fermentable=FermentableFactory(name="Orange Blossom Honey"), intended_use=use,
                                         amount=Quantity(12, "lb")),
        RecipeAdjunct.objects.create(adjunct=AdjunctFactory(name="Fermaid O"), intended_use=use,
                                     amount=Quantity(5, "gram"), time_to_add=Quantity(24, "hour")),
        RecipeYeasts.objects.create(yeast=YeastFactory(name="Lalvin 71B"), amount=Quantity(5, "gram")),
    ]
    for item in items:
        item.recipe.add(recipe)
    return recipe


@pytest.fixture
def batch(recipe):
    vessel = VesselFactory(status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(recipe=recipe, size=Quantity("12 gallons"), fermenter=FermenterFactory(vessel=vessel),
                         vessel=vessel)
    batch.startdate = timezone.now() - datetime.timedelta(days=2)
    batch.save()
    copy_recipe_ingredients_to_batch(batch)
    return batch


def _row(batch, name):
    return next(i for i in batch.ingredients.all() if i.name == name)


def _form_data(batch, changes=None, delete=(), add=None):
    """The edit page's POST: every current row (as shown), with changes / deletions / added rows."""
    changes, add = changes or {}, add or {}
    data = {}
    for kind, prefix, item_field in (("fermentable", "fermentable", "fermentable"), ("adjunct", "adjunct", "adjunct"),
                                     ("yeast", "yeast", "yeast")):
        rows = [i for i in batch.ingredients.all() if i.kind == kind]
        forms = []
        for row in rows:
            form = {"id": row.pk, "item_id": getattr(row, item_field).pk,
                    "amount": f"{row.amount.magnitude:g} {row.amount.units}" if row.amount is not None else "",
                    "notes": row.notes}
            if kind != "yeast":
                form["intended_use"] = row.intended_use_id or ""
            if kind == "adjunct":
                form["time_to_add"] = f"{row.time_to_add.to('hour').magnitude:g} hours" if row.time_to_add is not None else ""
            form.update(changes.get(row.name, {}))
            if row.name in delete:
                form["DELETE"] = "on"
            forms.append(form)
        forms += add.get(kind, [])
        data[f"{prefix}-TOTAL_FORMS"] = str(len(forms))
        data[f"{prefix}-INITIAL_FORMS"] = str(len(rows))
        for i, form in enumerate(forms):
            data.update({f"{prefix}-{i}-{key}": value for key, value in form.items()})
    return data


def _edit(client, batch, **kwargs):
    return client.post(reverse("editBatchRecipe", kwargs={"pk": batch.pk}), _form_data(batch, **kwargs))


def _pitch(batch):
    transition_stage_event(batch, BatchStage.objects.get(shortid="pitch"), timestamp=timezone.now())


# ---------- Before Pitch: editable ----------

@pytest.mark.django_db
def test_batch_recipe_page_offers_edit_until_pitch(client, batch):
    url = reverse("batchRecipe", kwargs={"pk": batch.pk})
    edit = reverse("editBatchRecipe", kwargs={"pk": batch.pk})

    assert edit in client.get(url).content.decode()
    _pitch(batch)
    page = client.get(url).content.decode()
    assert edit not in page and "Locked after Pitch" in page


@pytest.mark.django_db
def test_edit_page_shows_the_batch_rows(client, batch):
    page = client.get(reverse("editBatchRecipe", kwargs={"pk": batch.pk})).content.decode()

    assert 'name="fermentable-0-amount" value="24 lb"' in page
    assert 'name="adjunct-0-time_to_add"' in page
    assert 'name="yeast-0-amount" value="10 g"' in page


@pytest.mark.django_db
def test_changing_an_amount_and_timing_updates_the_batch_only(client, batch, recipe):
    response = _edit(client, batch, changes={"Orange Blossom Honey": {"amount": "30 lb"},
                                             "Fermaid O": {"amount": "12 g", "time_to_add": "48 hours"}})

    assert response.status_code == 302
    assert response["Location"] == reverse("batchRecipe", kwargs={"pk": batch.pk})
    honey, nutrient = _row(batch, "Orange Blossom Honey"), _row(batch, "Fermaid O")
    assert honey.amount.to("lb").magnitude == pytest.approx(30)
    assert honey.recipe_amount.to("lb").magnitude == pytest.approx(12)        # still compared to the recipe
    assert nutrient.amount.to("gram").magnitude == pytest.approx(12)
    assert nutrient.time_to_add.to("hour").magnitude == pytest.approx(48)
    # The library recipe is untouched.
    assert RecipeFermentable.objects.get(fermentable__name="Orange Blossom Honey").amount.to("lb").magnitude == pytest.approx(12)


@pytest.mark.django_db
def test_removing_and_adding_items(client, batch, use):
    tannin = AdjunctFactory(name="FT Rouge")

    _edit(client, batch, delete=("Lalvin 71B",),
          add={"adjunct": [{"item_id": tannin.pk, "amount": "4 g", "intended_use": use.pk,
                            "time_to_add": "0 hours", "notes": "Tannin at pitch"}]})

    names = [i.name for i in batch.ingredients.all()]
    assert "Lalvin 71B" not in names and "FT Rouge" in names
    added = _row(batch, "FT Rouge")
    assert added.kind == BatchIngredient.KIND_ADJUNCT and added.recipe_amount is None
    assert added.time_to_add.to("hour").magnitude == 0 and added.notes == "Tannin at pitch"


@pytest.mark.django_db
def test_an_edit_is_logged_on_the_batch(client, batch):
    _edit(client, batch, changes={"Orange Blossom Honey": {"amount": "30 lb"}}, delete=("Lalvin 71B",))

    entry = ActivityLog.objects.filter(text__startswith="Batch recipe edited").latest("datetime")
    assert "Orange Blossom Honey [24 lb] -> [30 lb]" in entry.text and "Removed Lalvin 71B" in entry.text


@pytest.mark.django_db
@pytest.mark.parametrize("amount, error", [("30", "Units are required."), ("30 days", "weight or volume unit")])
def test_a_bad_amount_is_rejected_and_nothing_changes(client, batch, amount, error):
    response = _edit(client, batch, changes={"Orange Blossom Honey": {"amount": amount}})

    assert response.status_code == 200 and error in response.content.decode()
    assert _row(batch, "Orange Blossom Honey").amount.to("lb").magnitude == pytest.approx(24)


@pytest.mark.django_db
def test_a_row_from_another_batch_cant_be_edited(client, batch, recipe):
    other = BatchFactory(recipe=recipe, size=Quantity("6 gallons"))
    copy_recipe_ingredients_to_batch(other)
    data = _form_data(batch)
    data["fermentable-0-id"] = _row(other, "Orange Blossom Honey").pk
    data["fermentable-0-amount"] = "99 lb"

    client.post(reverse("editBatchRecipe", kwargs={"pk": batch.pk}), data)

    assert _row(other, "Orange Blossom Honey").amount.to("lb").magnitude == pytest.approx(12)


# ---------- After Pitch: locked ----------

@pytest.mark.django_db
def test_after_pitch_the_edit_page_redirects_with_a_message(client, batch):
    _pitch(batch)

    response = client.get(reverse("editBatchRecipe", kwargs={"pk": batch.pk}), follow=True)

    assert response.redirect_chain[-1][0] == reverse("batchRecipe", kwargs={"pk": batch.pk})
    assert "can't be changed after Pitch" in html.unescape(response.content.decode())


@pytest.mark.django_db
def test_after_pitch_a_posted_edit_changes_nothing(client, batch):
    data = _form_data(batch, changes={"Orange Blossom Honey": {"amount": "30 lb"}})
    _pitch(batch)

    client.post(reverse("editBatchRecipe", kwargs={"pk": batch.pk}), data)

    assert _row(batch, "Orange Blossom Honey").amount.to("lb").magnitude == pytest.approx(24)


# ---------- Edit batch keeps the edits ----------

@pytest.mark.django_db
def test_a_size_correction_scales_the_edited_rows(client, batch, recipe):
    _edit(client, batch, changes={"Orange Blossom Honey": {"amount": "30 lb"}})

    save_batch_edit(batch, name=batch.name, recipe=recipe, size=Quantity("6 gallons"),
                    starting_gravity=1.100, estimated_end_gravity=1.010)

    assert _row(batch, "Orange Blossom Honey").amount.to("lb").magnitude == pytest.approx(15)


@pytest.mark.django_db
def test_switching_recipe_recopies_from_the_new_recipe(client, batch, use):
    other = RecipeFactory(batchSize=Quantity(6, "gallons"))
    line = RecipeFermentable.objects.create(fermentable=FermentableFactory(name="Wildflower Honey"), intended_use=use,
                                            amount=Quantity(10, "lb"))
    line.recipe.add(other)

    save_batch_edit(batch, name=batch.name, recipe=other, size=batch.size,
                    starting_gravity=1.100, estimated_end_gravity=1.010)

    assert [i.name for i in batch.ingredients.all()] == ["Wildflower Honey"]


@pytest.mark.django_db
def test_edit_requires_login(batch):
    response = Client().get(reverse("editBatchRecipe", kwargs={"pk": batch.pk}))

    assert response.status_code == 302 and "login" in response["Location"]
