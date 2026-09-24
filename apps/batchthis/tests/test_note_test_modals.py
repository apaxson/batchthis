"""Add note / Add test open in the shared #formModal on the batch page (like Add addon)."""
import json
import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from ..factories import BatchFactory
from ..models import BatchNoteType, BatchTestType

MODAL = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


@pytest.fixture
def batch(db):
    return BatchFactory()


def _now():
    return timezone.localtime().strftime("%Y-%m-%dT%H:%M")


def _note(**overrides):
    data = {"date": _now(), "notetype": BatchNoteType.objects.get(name="General Note").pk, "text": "Smells great"}
    data.update(overrides)
    return data


def _test(**overrides):
    data = {"datetime": _now(), "type": BatchTestType.objects.get(shortid="ph").pk, "value": "3.45", "description": ""}
    data.update(overrides)
    return data


FORMS = [
    ("addDetailNote", _note, "text", ""),
    ("addDetailTest", _test, "value", "high"),
]


# ---------- Modal requests ----------

@pytest.mark.django_db
@pytest.mark.parametrize("url_name, data, field, bad", FORMS)
def test_modal_get_returns_just_the_form_posting_back_to_its_page(client, batch, url_name, data, field, bad):
    url = reverse(url_name, kwargs={"pk": batch.pk})

    page = client.get(url, **MODAL).content.decode()

    assert "<html" not in page
    assert f'<form method="POST" class="cl-form" action="{url}"' in page


@pytest.mark.django_db
@pytest.mark.parametrize("url_name, data, field, bad", FORMS)
def test_modal_post_with_errors_returns_the_form_with_its_errors(client, batch, url_name, data, field, bad):
    response = client.post(reverse(url_name, kwargs={"pk": batch.pk}), data(**{field: bad}), **MODAL)

    page = response.content.decode()
    assert "<html" not in page
    assert 'aria-invalid="true"' in page


@pytest.mark.django_db
@pytest.mark.parametrize("url_name, data, field, bad", FORMS)
def test_modal_post_that_saves_replies_saved(client, batch, url_name, data, field, bad):
    response = client.post(reverse(url_name, kwargs={"pk": batch.pk}), data(), **MODAL)

    assert json.loads(response.content) == {"saved": True}


@pytest.mark.django_db
def test_note_type_from_the_url_is_preselected_in_the_modal(client, batch):
    url = reverse("addDetailNoteType", kwargs={"pk": batch.pk, "noteType": "Fermentation Note"})

    page = client.get(url, **MODAL).content.decode()

    fermentation = BatchNoteType.objects.get(name="Fermentation Note").pk
    assert re.search(rf'<option value="{fermentation}" selected>Fermentation Note</option>', page)
    assert f'action="{url}"' in page


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["addDetailNote", "addDetailTest"])
def test_full_pages_still_work_without_the_modal(client, batch, url_name):
    page = client.get(reverse(url_name, kwargs={"pk": batch.pk})).content.decode()

    assert "<html" in page and 'class="cl-form"' in page


# ---------- Add test inside the modal ----------

@pytest.mark.django_db
def test_test_form_carries_its_behavior_as_data_not_inline_script(client, batch):
    # Scripts in HTML inserted into the modal don't run, so the placeholder switch and
    # refractometer button are driven by cellar-ledger.js from data attributes.
    page = client.get(reverse("addDetailTest", kwargs={"pk": batch.pk}), **MODAL).content.decode()

    assert "<script" not in page
    assert "data-reading-examples" in page
    button = re.search(r"<button[^>]*value-refractometer-link[^>]*>", page).group(0)
    assert "data-cl-refractometer" in button
    assert 'data-toggle="modal"' not in button


# ---------- Batch page links ----------

@pytest.mark.django_db
def test_batch_page_opens_note_and_test_links_in_the_modal(client, batch):
    page = client.get(reverse("batch", kwargs={"pk": batch.pk})).content.decode()

    for url in (
        reverse("addDetailTest", kwargs={"pk": batch.pk}),
        reverse("addDetailNote", kwargs={"pk": batch.pk}),
        reverse("addDetailNoteType", kwargs={"pk": batch.pk, "noteType": "Fermentation Note"}).replace(" ", "%20"),
        reverse("addDetailNoteType", kwargs={"pk": batch.pk, "noteType": "General Note"}).replace(" ", "%20"),
    ):
        links = re.findall(rf'<a[^>]*href="{re.escape(url)}"[^>]*>', page)
        assert links, url
        assert all("data-cl-form-modal" in link for link in links), url
