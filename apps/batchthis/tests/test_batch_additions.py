"""Batch additions: Adjunct + weight/volume amount, and the Add addon modal."""
import json

import pytest
from django import forms
from django.contrib.auth import get_user_model
from django.db.models import ProtectedError
from django.test import Client
from django.urls import reverse

from ..factories import AdjunctFactory, BatchFactory
from ..fields import AmountField, DescriptiveQuantityField, VolumeField
from ..forms import BatchAdditionForm
from ..models import Adjunct, BatchAddition

MODAL = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


class _AmountForm(forms.Form):
    amount = AmountField()


def _clean(value):
    form = _AmountForm(data={"amount": value})
    form.is_valid()
    return form


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


def _url(batch):
    return reverse("addDetailAddon", kwargs={"pk": batch.pk})


# ---------- AmountField ----------

@pytest.mark.parametrize(
    "entered, magnitude, unit",
    [("4 grams", 4, "gram"), ("4g", 4, "gram"), ("0.5 oz", 0.5, "ounce"), ("2 lb", 2, "pound"),
     ("5 ml", 5, "milliliter"), ("1 tsp", 1, "teaspoon"), ("1 gallon", 1, "gallon")],
)
def test_amount_field_accepts_a_weight_or_volume_as_entered(entered, magnitude, unit):
    value = _clean(entered).cleaned_data["amount"]

    assert value.magnitude == pytest.approx(magnitude)
    assert str(value.units) == unit


def test_amount_without_units_says_units_are_required():
    assert _clean("4").errors["amount"] == ["Units are required."]


@pytest.mark.parametrize("entered", ["4 degF", "4 ppm", "4 days", "4 mg/L"])
def test_amount_that_is_not_a_weight_or_volume_is_rejected(entered):
    assert _clean(entered).errors["amount"] == ["Use a weight or volume unit, e.g. 4 grams or 5 ml."]


@pytest.mark.parametrize("entered", ["a pinch", "grams"])
def test_amount_that_is_not_a_measurement_is_rejected(entered):
    assert _clean(entered).errors["amount"] == ["Enter an amount and a unit, e.g. 4 grams or 5 ml."]


def test_amount_must_be_positive():
    assert "amount" in _clean("0 grams").errors


def test_volume_field_still_rejects_weights():
    class F(forms.Form):
        volume = VolumeField()

    form = F(data={"volume": "4 grams"})
    form.is_valid()
    assert form.errors["volume"] == ["Use a volume unit: gallons or liters."]


# ---------- Model ----------

def test_addition_amount_is_a_descriptive_quantity_and_adjunct_is_protected():
    assert isinstance(BatchAddition._meta.get_field("amount"), DescriptiveQuantityField)
    adjunct_fk = BatchAddition._meta.get_field("adjunct")
    assert adjunct_fk.related_model is Adjunct
    names = {f.name for f in BatchAddition._meta.get_fields()}
    assert "units" not in names and "name" not in names


@pytest.mark.django_db
@pytest.mark.parametrize("entered, shown", [("4 grams", "4.00 gram"), ("5 ml", "5.00 milliliter"), ("0.5 oz", "0.50 ounce")])
def test_addition_amount_is_returned_as_entered(entered, shown):
    addition = BatchAddition.objects.create(batch=BatchFactory(), adjunct=AdjunctFactory(), amount=entered)

    addition.refresh_from_db()

    assert str(addition.amount) == shown


@pytest.mark.django_db
def test_an_adjunct_used_in_a_batch_cannot_be_deleted():
    adjunct = AdjunctFactory()
    BatchAddition.objects.create(batch=BatchFactory(), adjunct=adjunct, amount="4 grams")

    with pytest.raises(ProtectedError):
        adjunct.delete()


@pytest.mark.django_db
def test_adding_an_addition_writes_the_activity_log_with_the_adjunct_and_amount():
    batch = BatchFactory()

    BatchAddition.objects.create(batch=batch, adjunct=AdjunctFactory(name="Fermaid-O", supplier=None), amount="4 grams")

    assert batch.activity.filter(text="Added [Fermaid-O] :: 4.00 gram").exists()


# ---------- Form ----------

@pytest.mark.django_db
def test_addition_form_picks_adjuncts_with_the_react_select_picker(client):
    adjunct = AdjunctFactory()
    batch = BatchFactory()

    response = client.get(_url(batch))

    form = response.context["form"]
    assert list(form.fields) == ["adjunct", "amount", "description"]
    assert adjunct in form.fields["adjunct"].queryset
    assert isinstance(form.fields["adjunct"].widget, forms.HiddenInput)
    page = response.content.decode()
    assert "data-react-model-select" in page
    assert f'data-endpoint="{reverse("adjunct-list")}"' in page
    assert isinstance(BatchAdditionForm.base_fields["amount"], AmountField)


# ---------- Modal ----------

@pytest.mark.django_db
def test_modal_get_returns_just_the_form(client):
    response = client.get(_url(BatchFactory()), **MODAL)

    page = response.content.decode()
    assert response.status_code == 200
    assert "<html" not in page
    assert 'class="cl-form"' in page
    assert "data-react-model-select" in page


@pytest.mark.django_db
def test_modal_post_with_errors_returns_the_form_with_its_errors(client):
    batch = BatchFactory()

    response = client.post(_url(batch), {"adjunct": AdjunctFactory().pk, "amount": "4", "description": ""}, **MODAL)

    page = response.content.decode()
    assert response.status_code == 200
    assert "<html" not in page
    assert "Units are required." in page
    assert 'aria-invalid="true"' in page
    assert not batch.additions.exists()


@pytest.mark.django_db
def test_modal_post_that_saves_replies_saved_for_a_page_reload(client):
    batch = BatchFactory()

    response = client.post(_url(batch), {"adjunct": AdjunctFactory().pk, "amount": "4 grams", "description": ""}, **MODAL)

    assert response.status_code == 200
    assert json.loads(response.content) == {"saved": True}
    assert batch.additions.get().amount.magnitude == pytest.approx(4)


@pytest.mark.django_db
def test_without_the_modal_the_page_still_works(client):
    batch = BatchFactory()

    page = client.get(_url(batch)).content.decode()
    response = client.post(_url(batch), {"adjunct": AdjunctFactory().pk, "amount": "4 grams", "description": ""})

    assert "<html" in page
    assert response.status_code == 302
    assert response["Location"] == reverse("batch", kwargs={"pk": batch.pk})


@pytest.mark.django_db
def test_batch_page_opens_add_addon_in_the_shared_modal(client):
    batch = BatchFactory()

    page = client.get(reverse("batch", kwargs={"pk": batch.pk})).content.decode()

    assert f'href="{_url(batch)}" data-cl-form-modal' in page
    assert 'id="formModal"' in page
    assert "model-select.js" in page
