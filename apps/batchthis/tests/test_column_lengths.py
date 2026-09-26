"""
PostgreSQL enforces CharField max_length; SQLite never did, so values too long for
their column slipped in unnoticed (found moving to the Docker Postgres, 2026-09-26).
"""
import pytest
from django.apps import apps
from django.db import models

from ..factories import AdjunctFactory, FermentableFactory, YeastFactory
from ..models import FermentableType, RecipeAdjunct, Unit, Yeast


def _too_long() -> list[str]:
    problems = []
    for model in apps.get_models():
        for field in model._meta.concrete_fields:
            if isinstance(field, models.CharField) and field.max_length:
                for pk, value in model.objects.values_list("pk", field.attname):
                    if value and len(value) > field.max_length:
                        problems.append(f"{model.__name__}.{field.name} pk={pk}: {len(value)} > {field.max_length}")
    return problems


@pytest.mark.django_db
def test_every_value_the_migrations_seed_fits_its_column():
    # e.g. 0002 seeds Unit.identifier "specific-gravity" (16 characters).
    assert _too_long() == []


@pytest.mark.django_db
def test_beersmith_length_text_fits():
    long_text = "x" * 1000
    AdjunctFactory(description=long_text)
    FermentableFactory(description=long_text)
    YeastFactory(description=long_text, flocculation="Very High")
    FermentableType.objects.create(name="Dry Extract")

    assert _too_long() == []
    assert RecipeAdjunct._meta.get_field("recipe_notes").max_length is None  # TextField
    assert Unit._meta.get_field("identifier").max_length >= len("specific-gravity")


@pytest.mark.django_db
def test_a_yeast_form_can_be_unknown():
    yeast = YeastFactory(form="")

    yeast.full_clean(exclude=["supplier"])
    assert yeast.get_form_display() == ""
    assert Yeast._meta.get_field("form").blank
