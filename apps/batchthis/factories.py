import factory

from .models import Fermentable, FermentableType


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
