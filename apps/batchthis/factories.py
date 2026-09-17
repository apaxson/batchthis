import factory
from pint import Quantity

from .models import (
    Adjunct,
    AdjunctType,
    AdjunctUsage,
    BatchCategory,
    BatchStyle,
    BatchTestType,
    Fermentable,
    FermentableType,
    Fermenter,
    Recipe,
    Unit,
    Vessel,
    Yeast,
)


class FermentableTypeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = FermentableType

    name = factory.Sequence(lambda n: f"Type{n}")


class FermentableFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Fermentable

    name = factory.Sequence(lambda n: f"Fermentable{n}")
    supplier = factory.Faker("company")
    type = factory.SubFactory(FermentableTypeFactory)
    sugar_content = 20.0
    potential = 1.035


class AdjunctTypeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AdjunctType

    name = factory.Sequence(lambda n: f"AdjunctType{n}")


class AdjunctUsageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AdjunctUsage

    name = factory.Sequence(lambda n: f"Usage{n}")


class AdjunctFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Adjunct

    name = factory.Sequence(lambda n: f"Adjunct{n}")
    supplier = factory.Faker("company")
    type = factory.SubFactory(AdjunctTypeFactory)
    use = factory.SubFactory(AdjunctUsageFactory)


class YeastFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Yeast

    name = factory.Sequence(lambda n: f"Yeast{n}")
    supplier = factory.Faker("company")
    type = "Ale"
    form = "dry"
    min_temp = Quantity(18, "degC")
    max_temp = Quantity(24, "degC")
    flocculation = "Medium"
    attenuation = 75.0


class UnitFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Unit

    identifier = "sg"
    label = "SG"
    name = "Specific Gravity"
    category = Unit.CONCENTRATION


class VesselFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Vessel

    name = factory.Sequence(lambda n: f"Vessel{n}")
    max_size = 6
    max_size_units = factory.SubFactory(UnitFactory, identifier="gal", label="gal", name="Gallons", category=Unit.VOLUME)
    status = Vessel.STATUS_READY
    intended_use = "PRI"


class FermenterFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Fermenter

    vessel = factory.SubFactory(VesselFactory)


class BatchTestTypeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = BatchTestType

    name = "Specific Gravity"
    shortid = "specific-gravity"


class BatchStyleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = BatchStyle

    name = factory.Sequence(lambda n: f"Style{n}")


class BatchCategoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = BatchCategory

    name = factory.Sequence(lambda n: f"Category{n}")
    style = factory.SubFactory(BatchStyleFactory)
    bjcp_code = "A1"


class RecipeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Recipe

    name = factory.Sequence(lambda n: f"Recipe{n}")
    dateCreated = factory.Faker("date")
    category = factory.SubFactory(BatchCategoryFactory)
    brewer = "Test Brewer"
    batchSize = Quantity(6, "gallons")
    notes = ""
    estOG = Quantity(1.09, "sg")
    estFG = Quantity(1.0, "sg")
    estABV = 12.0
