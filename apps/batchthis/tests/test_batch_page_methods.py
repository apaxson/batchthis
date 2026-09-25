"""
The batch page only reads (Aaron, 2026-09-25): GET/HEAD render it; anything else
is 405 Method Not Allowed. The view used to return None for a POST - a server error.
"""
import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from ..factories import BatchFactory


@pytest.fixture
def client(db):
    client = Client(raise_request_exception=False)
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_batch_page_rejects_writes_with_405(client, method):
    batch = BatchFactory()

    response = getattr(client, method)(reverse("batch", kwargs={"pk": batch.pk}))

    assert response.status_code == 405
    assert set(response["Allow"].split(", ")) == {"GET", "HEAD"}


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["get", "head"])
def test_batch_page_still_reads(client, method):
    batch = BatchFactory()

    response = getattr(client, method)(reverse("batch", kwargs={"pk": batch.pk}))

    assert response.status_code == 200
