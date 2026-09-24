import factory
from pint import Quantity

from .models import (
    Adjunct,
    AdjunctType,
    AdjunctUsage,
    Batch,
    BatchCategory,
    BatchStage,
    BatchStageEvent,
    BatchStyle,
    BatchTestType,
    Fermentable,
    FermentableType,
    Fermenter,
    Recipe,
    RecipePlanStep,
    WorkflowTemplate,
    WorkflowTemplateStep,
    Unit,
    Vessel,
    VesselStatusEvent,
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
    capacity = "6 gallons"
    status = Vessel.STATUS_READY
    intended_use = "PRI"


class VesselStatusEventFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = VesselStatusEvent

    vessel = factory.SubFactory(VesselFactory)
    status = Vessel.STATUS_READY


class FermenterFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Fermenter

    vessel = factory.SubFactory(VesselFactory)


class BatchStageFactory(factory.django.DjangoModelFactory):
    # The six real workflow stages are seeded by migration 0035_default_load2;
    # use this for extra, throwaway stages in tests.
    class Meta:
        model = BatchStage

    name = factory.Sequence(lambda n: f"Stage{n}")
    sort_order = factory.Sequence(lambda n: 100 + n)
    from_state = BatchStage.STATE_AGING
    to_state = BatchStage.STATE_AGING


class BatchStageEventFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = BatchStageEvent

    batch = factory.SubFactory("apps.batchthis.factories.BatchFactory")
    # Seeded by migration 0035_default_load2.
    stage = factory.LazyFunction(lambda: BatchStage.objects.get(shortid="pitch"))


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


class WorkflowTemplateFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = WorkflowTemplate

    name = factory.Sequence(lambda n: f"Template {n}")


class _PlanStepFields(factory.django.DjangoModelFactory):
    class Meta:
        abstract = True

    sort_order = factory.Sequence(lambda n: n + 1)
    # Seeded by migration 0035_default_load2.
    stage = factory.LazyFunction(lambda: BatchStage.objects.get(shortid="pitch"))
    planned_duration = "14 days"


class WorkflowTemplateStepFactory(_PlanStepFields):
    class Meta:
        model = WorkflowTemplateStep

    template = factory.SubFactory(WorkflowTemplateFactory)


class RecipePlanStepFactory(_PlanStepFields):
    class Meta:
        model = RecipePlanStep

    recipe = factory.SubFactory(RecipeFactory)


class BatchFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Batch

    name = factory.Sequence(lambda n: f"Batch{n}")
    size = Quantity(6, "gallons")
    fermenter = factory.SubFactory(FermenterFactory)
    startingGravity = Quantity(1.09, "sg")
    estimatedEndGravity = Quantity(1.005, "sg")
