import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from ..factories import AdjunctFactory, AdjunctTypeFactory


@pytest.fixture
def api_client(db):
    user = get_user_model().objects.create_user(username="taster", password="password123")
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_adjunct_list_returns_serialized_adjuncts(api_client):
    adjunct_type = AdjunctTypeFactory(name="Nutrient")
    adjunct = AdjunctFactory(
        name="Fermaid O", supplier="Local Supply Co", type=adjunct_type
    )

    response = api_client.get(reverse("adjunct-list"))

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    entry = payload[0]
    assert entry["id"] == adjunct.id
    assert entry["display_name"] == "Local Supply Co: Fermaid O"
    assert entry["type"] == adjunct_type.id
    assert entry["supplier"] == "Local Supply Co"


@pytest.mark.django_db
def test_adjunct_list_requires_authentication():
    client = APIClient()

    response = client.get(reverse("adjunct-list"))

    assert response.status_code == 401 or response.status_code == 403