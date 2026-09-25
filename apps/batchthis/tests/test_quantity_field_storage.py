"""
DescriptiveQuantityField stores text markup ("22.71:gallon"), so its column must
be a text column - not the REAL column django-pint's QuantityField (a FloatField)
declares. SQLite tolerated text in a REAL column; PostgreSQL/MySQL reject it.
"""
import pytest
from django.apps import apps
from django.db import connection
from pint import Quantity

from ..factories import VesselFactory, YeastFactory
from ..fields import DescriptiveQuantityField
from ..models import BatchTest, Vessel, Yeast


def _descriptive_fields():
    return [
        (model, field)
        for model in apps.get_app_config("batchthis").get_models()
        for field in model._meta.concrete_fields
        if isinstance(field, DescriptiveQuantityField)
    ]


def test_every_descriptive_quantity_field_is_a_text_column():
    fields = _descriptive_fields()

    assert fields, "expected DescriptiveQuantityField columns in batchthis"
    for model, field in fields:
        assert field.get_internal_type() == "TextField", f"{model.__name__}.{field.name}"
        assert field.db_type(connection) == connection.data_types["TextField"], f"{model.__name__}.{field.name}"


@pytest.mark.django_db
def test_the_migrated_tables_have_text_columns():
    with connection.cursor() as cursor:
        for model, field in _descriptive_fields():
            description = connection.introspection.get_table_description(cursor, model._meta.db_table)
            column = next(c for c in description if c.name == field.column)
            assert connection.introspection.get_field_type(column.type_code, column) == "TextField", (
                f"{model._meta.db_table}.{field.column}"
            )


@pytest.mark.django_db
def test_markup_longer_than_30_characters_round_trips():
    # 68 degF -> "20.000000000000004:degree_Fahrenheit" (36 chars) - past the old max_length of 30.
    yeast = YeastFactory(min_temp=Quantity(68, "degF"), max_temp=Quantity(77, "degF"))
    stored = Yeast._meta.get_field("min_temp").get_prep_value(Quantity(68, "degF"))
    assert len(stored) > 30

    yeast.refresh_from_db()

    assert yeast.min_temp.magnitude == pytest.approx(68)
    assert str(yeast.min_temp.units) == "degree_Fahrenheit"


@pytest.mark.django_db
def test_markup_is_stored_as_text_in_the_database():
    vessel = VesselFactory(capacity="6 gallons")

    with connection.cursor() as cursor:
        cursor.execute(f"SELECT capacity FROM {Vessel._meta.db_table} WHERE id = %s", [vessel.pk])
        raw = cursor.fetchone()[0]

    assert isinstance(raw, str)
    magnitude, unit = raw.split(":")
    assert float(magnitude) == pytest.approx(22.712, abs=0.001)
    assert unit == "gallon"


def test_a_bare_number_from_an_old_row_still_reads_as_base_units():
    # Rows written before markup (or a numeric value read back by the database driver).
    field = BatchTest._meta.get_field("value")

    for raw in (1.09, "1.09"):
        value = field.from_db_value(raw)
        assert value.magnitude == pytest.approx(1.09)
        assert value.dimensionless
