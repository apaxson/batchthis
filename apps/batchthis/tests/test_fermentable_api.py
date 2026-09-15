import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from ..factories import FermentableFactory, FermentableTypeFactory


@pytest.fixture
def api_client(db):
    user = get_user_model().objects.create_user(username="taster", password="password123")
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_fermentable_list_returns_serialized_fermentables(api_client):
    fermentable_type = FermentableTypeFactory(name="Honey")
    fermentable = FermentableFactory(
        name="Wildflower Honey", supplier="Local Apiary", type=fermentable_type
    )

    response = api_client.get(reverse("fermentable-list"))

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    entry = payload[0]
    assert entry["id"] == fermentable.id
    assert entry["display_name"] == "Local Apiary: Wildflower Honey"
    assert entry["type"] == fermentable_type.id
    assert entry["supplier"] == "Local Apiary"


@pytest.mark.django_db
def test_fermentable_list_requires_authentication():
    client = APIClient()

    response = client.get(reverse("fermentable-list"))

    assert response.status_code == 401 or response.status_code == 403
