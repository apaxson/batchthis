import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from ..factories import YeastFactory


@pytest.fixture
def api_client(db):
    user = get_user_model().objects.create_user(username="taster", password="password123")
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_yeast_list_returns_serialized_yeasts(api_client):
    yeast = YeastFactory(name="EC-1118", supplier="Lalvin", type="Wine")

    response = api_client.get(reverse("yeast-list"))

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    entry = payload[0]
    assert entry["id"] == yeast.id
    assert entry["display_name"] == "Lalvin: EC-1118"
    assert entry["type"] == "Wine"
    assert entry["supplier"] == "Lalvin"


@pytest.mark.django_db
def test_yeast_list_requires_authentication():
    client = APIClient()

    response = client.get(reverse("yeast-list"))

    assert response.status_code == 401 or response.status_code == 403