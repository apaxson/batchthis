"""Plan steps name a vessel TYPE ("any clean <type>"), required - TODO-BatchStage.txt UNIFIED PLAN DESIGN."""
import pytest

from ..factories import (
    FermenterFactory,
    RecipeFactory,
    RecipePlanStepFactory,
    WorkflowTemplateFactory,
    WorkflowTemplateStepFactory,
)
from ..models import BatchStage, PlanStep, RecipePlanStep, Vessel, WorkflowTemplateStep
from ..services import VESSEL_TYPES, copy_template_to_recipe, plan_problems

FERMENTER, AGING_TANK, BARREL, CURRENT = (
    Vessel.TYPE_FERMENTER, Vessel.TYPE_AGING_TANK, Vessel.TYPE_BARREL, PlanStep.VESSEL_CURRENT,
)


def _stage(shortid):
    return BatchStage.objects.get(shortid=shortid)


def _plan(*rows):
    """(shortid, duration, vessel type) rows -> plan_problems() input."""
    return [(_stage(shortid), duration, vessel_type) for shortid, duration, vessel_type in rows]


GOOD_PLAN = [
    ("pitch", "14 days", FERMENTER),
    ("racking", "30 days", AGING_TANK),
    ("racking", "2 months", BARREL),
    ("coarse-filtering", None, AGING_TANK),
    ("fine-filtering", "1 week", FERMENTER),
    ("sterile-filtering", "5 days", AGING_TANK),
    ("complete-batch", None, CURRENT),
]


# ---------- One list of vessel types ----------

def test_vessel_types_are_defined_once_on_vessel():
    assert [value for value, _ in Vessel.TYPE_CHOICES] == ["Fermenter", "Aging Tank", "Barrel"]
    assert list(VESSEL_TYPES) == [value for value, _ in Vessel.TYPE_CHOICES]


def test_plan_step_vessel_type_is_required_with_a_none_current_option():
    for model in (WorkflowTemplateStep, RecipePlanStep):
        field = model._meta.get_field("vessel_type")
        assert not field.blank and not field.null
        assert field.choices == Vessel.TYPE_CHOICES + [(CURRENT, "None / Current")]
        assert "vessel_role" not in {f.name for f in model._meta.get_fields()}


@pytest.mark.django_db
def test_vessel_type_displays_its_label():
    step = RecipePlanStepFactory(stage=_stage("complete-batch"), planned_duration=None, vessel_type=CURRENT)

    assert step.get_vessel_type_display() == "None / Current"


@pytest.mark.django_db
def test_vessels_still_report_their_type_with_the_shared_names():
    assert FermenterFactory().vessel.vessel_type == FERMENTER


# ---------- plan_problems(): the per-step vessel type rules ----------

@pytest.mark.django_db
def test_a_plan_following_the_vessel_type_rules_is_valid():
    assert plan_problems(_plan(*GOOD_PLAN)) == []


@pytest.mark.django_db
@pytest.mark.parametrize("vessel_type", [AGING_TANK, BARREL, CURRENT])
def test_pitch_always_starts_in_a_fermenter(vessel_type):
    problems = plan_problems(_plan(("pitch", "14 days", vessel_type)))

    assert problems == ["Step 1: Pitch always starts in a Fermenter."]


@pytest.mark.django_db
@pytest.mark.parametrize("shortid", ["racking", "coarse-filtering", "fine-filtering", "sterile-filtering"])
def test_steps_that_move_the_batch_need_a_real_vessel_type(shortid):
    rows = [("pitch", "14 days", FERMENTER)]
    if shortid != "racking":
        rows.append(("racking", "30 days", AGING_TANK))
    rows.append((shortid, "7 days", CURRENT))

    problems = plan_problems(_plan(*rows))

    name = _stage(shortid).name
    assert problems == [f"Step {len(rows)}: {name} moves the batch - choose Fermenter, Aging Tank or Barrel."]


@pytest.mark.django_db
@pytest.mark.parametrize("vessel_type", [FERMENTER, AGING_TANK, BARREL])
def test_complete_batch_is_always_none_current(vessel_type):
    rows = [("pitch", "14 days", FERMENTER), ("racking", "30 days", AGING_TANK),
            ("sterile-filtering", "5 days", AGING_TANK), ("complete-batch", None, vessel_type)]

    assert plan_problems(_plan(*rows)) == ["Step 4: Complete Batch ends the batch - its vessel type is None / Current."]


@pytest.mark.django_db
@pytest.mark.parametrize("missing", ["", None, "Brite Tank"])
def test_every_step_needs_a_known_vessel_type(missing):
    assert plan_problems(_plan(("pitch", "14 days", missing))) == ["Step 1: choose a vessel type."]


@pytest.mark.django_db
def test_saved_steps_are_checked_for_vessel_types_too():
    recipe = RecipeFactory()
    RecipePlanStepFactory(recipe=recipe, sort_order=1, stage=_stage("pitch"), vessel_type=AGING_TANK)

    assert plan_problems(recipe.plan_steps.all()) == ["Step 1: Pitch always starts in a Fermenter."]


@pytest.mark.django_db
def test_order_and_vessel_type_problems_are_both_reported():
    problems = plan_problems(_plan(("pitch", "14 days", BARREL), ("sterile-filtering", None, CURRENT)))

    assert problems == [
        "Step 1: Pitch always starts in a Fermenter.",
        ("Step 2: Sterile Filtering can't come after Pitch - the batch would be in Fermentation, "
         "and Sterile Filtering needs Aging."),
        "Step 2: Sterile Filtering moves the batch - choose Fermenter, Aging Tank or Barrel.",
    ]


# ---------- Copying a template keeps the vessel types ----------

@pytest.mark.django_db
def test_copying_a_template_copies_each_steps_vessel_type():
    template = WorkflowTemplateFactory()
    for order, (shortid, duration, vessel_type) in enumerate(GOOD_PLAN, start=1):
        WorkflowTemplateStepFactory(template=template, sort_order=order, stage=_stage(shortid),
                                    planned_duration=duration, vessel_type=vessel_type)
    recipe = RecipeFactory()

    copy_template_to_recipe(template, recipe)

    assert [s.vessel_type for s in recipe.plan_steps.all()] == [vessel_type for _, _, vessel_type in GOOD_PLAN]


@pytest.mark.django_db
def test_factories_default_to_a_valid_pitch_step():
    step = RecipePlanStepFactory()

    assert (step.stage.shortid, step.vessel_type) == ("pitch", FERMENTER)
    assert plan_problems([step]) == []
