import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from ..factories import FermenterFactory, RecipeFactory
from ..models import Batch


def _logged_in_client() -> Client:
    # Login is required site-wide (LoginRequiredMiddleware).
    client = Client()
    client.force_login(get_user_model().objects.get_or_create(username="cellarhand")[0])
    return client


@pytest.mark.django_db
def test_add_batch_auto_creates_specific_gravity_test():
    # addGravityTest (models.py) relies on the 'specific-gravity' BatchTestType and
    # "Specific Gravity" Unit rows loaded by the 0002_default_load data migration.
    fermenter = FermenterFactory()
    recipe = RecipeFactory(with_plan=True)

    client = _logged_in_client()
    response = client.post(
        reverse("addBatch"),
        data={
            "name": "test recipe (6Gallons) 2026-09-17",
            "startdate": "2026-09-17",
            "size": "6Gallons",
            "fermenter": fermenter.pk,
            "startingGravity": "1.09",
            "estimatedEndGravity": "1.005",
            "recipe": recipe.pk,
        },
    )

    assert response.status_code == 302
    batch = Batch.objects.get(name="test recipe (6Gallons) 2026-09-17")
    assert batch.startingGravity.magnitude == pytest.approx(1.09)

    gravity_test = batch.tests.get(type__shortid="specific-gravity")
    assert gravity_test.chart_value == pytest.approx(1.09)
