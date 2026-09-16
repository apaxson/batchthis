import factory
from pint import Quantity

from .models import Adjunct, AdjunctType, AdjunctUsage, Fermentable, FermentableType, Yeast


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
