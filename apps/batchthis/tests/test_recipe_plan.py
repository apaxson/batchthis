"""Phase 2: per-step plans (workflow templates and recipes) - TODO-BatchStage.txt "UNIFIED PLAN DESIGN"."""
import pytest
from django import forms
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import RequestFactory

from ..factories import (
    RecipeFactory,
    RecipePlanStepFactory,
    WorkflowTemplateFactory,
    WorkflowTemplateStepFactory,
)
from ..fields import DescriptiveQuantityField, TimeSpanField
from ..models import (
    BatchStage,
    PlanStep,
    RecipePlanStep,
    Vessel,
    WorkflowTemplate,
    WorkflowTemplateStep,
)
from ..services import copy_template_to_recipe, plan_problems, plan_totals

FERMENTATION, AGING, BOTTLING = BatchStage.STATE_FERMENTATION, BatchStage.STATE_AGING, BatchStage.STATE_BOTTLING


def _stage(shortid):
    return BatchStage.objects.get(shortid=shortid)


def _plan(*shortids_and_durations):
    return [(_stage(shortid), duration) for shortid, duration in shortids_and_durations]


# A valid vessel type per stage (tests/test_plan_vessel_types.py has the rules).
VESSEL_TYPE_FOR = {
    "pitch": Vessel.TYPE_FERMENTER, "racking": Vessel.TYPE_AGING_TANK, "coarse-filtering": Vessel.TYPE_AGING_TANK,
    "fine-filtering": Vessel.TYPE_AGING_TANK, "sterile-filtering": Vessel.TYPE_AGING_TANK,
    "complete-batch": PlanStep.VESSEL_CURRENT,
}

FULL_PLAN = [
    ("pitch", "14 days"), ("racking", "30 days"), ("racking", "2 months"),
    ("coarse-filtering", None), ("fine-filtering", "1 week"),
    ("sterile-filtering", "0 days"), ("complete-batch", None),
]


# ---------- TimeSpanField ----------

class _SpanForm(forms.Form):
    span = TimeSpanField(required=False)


def _span(value):
    form = _SpanForm(data={"span": value})
    form.is_valid()
    return form


@pytest.mark.parametrize("entered, days", [("14 days", 14), ("2 weeks", 14), ("36 hours", 1.5), ("0 days", 0)])
def test_timespan_field_accepts_a_duration_including_zero(entered, days):
    assert _span(entered).cleaned_data["span"].to("day").magnitude == pytest.approx(days)


def test_timespan_field_keeps_the_unit_entered_and_blank_is_none():
    assert str(_span("2 weeks").cleaned_data["span"].units) == "week"
    assert _span("").cleaned_data["span"] is None


def test_timespan_errors():
    assert _span("14").errors["span"] == ["Units are required."]
    assert _span("14 g").errors["span"] == ["Use a time unit, e.g. 14 days or 2 weeks."]
    assert "span" in _span("-3 days").errors


# ---------- Models ----------

def test_template_steps_and_recipe_steps_share_one_step_shape():
    for model in (WorkflowTemplateStep, RecipePlanStep):
        assert issubclass(model, PlanStep)
        names = {f.name for f in model._meta.get_fields()}
        assert {"sort_order", "stage", "planned_duration", "vessel_type", "notes"} <= names
        duration = model._meta.get_field("planned_duration")
        assert isinstance(duration, DescriptiveQuantityField) and duration.null


@pytest.mark.django_db
@pytest.mark.parametrize("entered, days, counted", [("2 weeks", 14, True), ("0 days", None, False), (None, None, False)])
def test_a_step_with_no_or_zero_duration_is_not_counted(entered, days, counted):
    step = RecipePlanStepFactory(stage=_stage("racking"), planned_duration=entered)

    step.refresh_from_db()

    assert step.has_duration is counted
    assert step.planned_days == (pytest.approx(days) if days else None)


@pytest.mark.django_db
def test_steps_come_back_in_order():
    template = WorkflowTemplateFactory()
    second = WorkflowTemplateStepFactory(template=template, sort_order=2, stage=_stage("racking"))
    first = WorkflowTemplateStepFactory(template=template, sort_order=1, stage=_stage("pitch"))

    assert list(template.steps.all()) == [first, second]


@pytest.mark.django_db
def test_template_names_are_unique():
    WorkflowTemplateFactory(name="Traditional mead")

    with pytest.raises(IntegrityError):
        WorkflowTemplateFactory(name="Traditional mead")


@pytest.mark.django_db
def test_deleting_a_template_keeps_recipes_that_copied_it():
    template = WorkflowTemplateFactory()
    WorkflowTemplateStepFactory(template=template, stage=_stage("pitch"))
    recipe = RecipeFactory()
    copy_template_to_recipe(template, recipe)

    template.delete()

    recipe.refresh_from_db()
    assert recipe.workflow_template is None
    assert recipe.plan_steps.count() == 1


@pytest.mark.django_db
def test_a_stage_used_in_a_plan_cannot_be_deleted():
    RecipePlanStepFactory(stage=_stage("pitch"))

    with pytest.raises(ProtectedError):
        _stage("pitch").delete()


@pytest.mark.django_db
@pytest.mark.parametrize("model", [WorkflowTemplate, WorkflowTemplateStep, RecipePlanStep])
def test_plan_models_are_view_only_in_admin(model):
    request = RequestFactory().get("/admin/")
    request.user = get_user_model().objects.create_superuser("root", "root@example.com", "pw")
    model_admin = admin.site._registry[model]

    assert model_admin.has_view_permission(request)
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request)
    assert not model_admin.has_delete_permission(request)


# ---------- plan_problems(): order is always checked, durations are optional ----------

@pytest.mark.django_db
def test_a_plan_mixing_timed_and_point_in_time_steps_is_valid():
    assert plan_problems(_plan(*FULL_PLAN)) == []


@pytest.mark.django_db
def test_an_empty_plan_and_a_plan_with_no_durations_are_valid():
    assert plan_problems([]) == []
    assert plan_problems(_plan(("pitch", None), ("racking", None), ("sterile-filtering", None))) == []


@pytest.mark.django_db
def test_the_plan_must_start_with_pitch():
    assert plan_problems(_plan(("racking", "30 days"))) == ["Step 1: the plan must start with Pitch."]


@pytest.mark.django_db
def test_point_in_time_steps_are_still_checked_for_order():
    problems = plan_problems(_plan(("pitch", "14 days"), ("sterile-filtering", None)))

    assert problems == [
        ("Step 2: Sterile Filtering can't come after Pitch - the batch would be in Fermentation, "
         "and Sterile Filtering needs Aging."),
    ]


@pytest.mark.django_db
def test_nothing_can_follow_complete_batch():
    assert plan_problems(_plan(*FULL_PLAN, ("racking", "30 days"))) == ["Step 8: nothing can come after Complete Batch."]


@pytest.mark.django_db
def test_complete_batch_is_always_point_in_time():
    base = [("pitch", "14 days"), ("racking", "30 days"), ("sterile-filtering", "5 days")]

    assert plan_problems(_plan(*base, ("complete-batch", "0 days"))) == []
    assert plan_problems(_plan(*base, ("complete-batch", "1 day"))) == [
        "Step 4: Complete Batch ends the batch - leave its duration blank.",
    ]


def test_live_workflow_and_plans_share_the_order_rule():
    racking = BatchStage(shortid="racking", from_state=BatchStage.STATE_FERMENTATION, to_state=BatchStage.STATE_AGING)

    assert racking.can_follow(BatchStage.STATE_FERMENTATION)
    assert racking.can_follow(BatchStage.STATE_AGING)  # repeat racking
    assert not racking.can_follow(None)
    assert not racking.can_follow(BatchStage.STATE_COMPLETED)


# ---------- plan_totals(): only steps with a duration count ----------

@pytest.mark.django_db
def test_totals_sum_only_steps_with_a_duration_per_state():
    totals = plan_totals(_plan(*FULL_PLAN))

    # 14 (pitch) + 30 + ~60.9 (2 months) + 7 (fine) ; coarse=None and sterile=0 skipped
    assert totals.by_state[FERMENTATION] == pytest.approx(14)
    assert totals.by_state[AGING] == pytest.approx(30 + 60.87 + 7, abs=0.1)
    assert BOTTLING not in totals.by_state
    assert totals.total_days == pytest.approx(sum(totals.by_state.values()))


@pytest.mark.django_db
def test_totals_of_a_plan_with_no_durations_are_zero():
    totals = plan_totals(_plan(("pitch", None), ("racking", "0 days")))

    assert totals.total_days == 0
    assert totals.by_state == {}


@pytest.mark.django_db
def test_totals_accept_saved_steps_too():
    recipe = RecipeFactory()
    RecipePlanStepFactory(recipe=recipe, sort_order=1, stage=_stage("pitch"), planned_duration="2 weeks")
    RecipePlanStepFactory(recipe=recipe, sort_order=2, stage=_stage("racking"), planned_duration=None)

    totals = plan_totals(recipe.plan_steps.all())

    assert totals.total_days == pytest.approx(14)


# ---------- Recipe picks a template: its own copy + which template ----------

@pytest.mark.django_db
def test_copying_a_template_gives_the_recipe_its_own_steps_and_remembers_the_template():
    template = WorkflowTemplateFactory(name="Traditional mead")
    for order, (shortid, duration) in enumerate(FULL_PLAN, start=1):
        WorkflowTemplateStepFactory(template=template, sort_order=order, stage=_stage(shortid),
                                    planned_duration=duration, vessel_type=VESSEL_TYPE_FOR[shortid],
                                    notes=f"note {order}")
    recipe = RecipeFactory()

    copy_template_to_recipe(template, recipe)

    recipe.refresh_from_db()
    assert recipe.workflow_template == template
    copied = [(s.sort_order, s.stage.shortid, s.planned_days, s.vessel_type, s.notes) for s in recipe.plan_steps.all()]
    original = [(s.sort_order, s.stage.shortid, s.planned_days, s.vessel_type, s.notes) for s in template.steps.all()]
    assert copied == original


@pytest.mark.django_db
def test_editing_the_template_later_does_not_change_the_recipe():
    template = WorkflowTemplateFactory()
    step = WorkflowTemplateStepFactory(template=template, stage=_stage("pitch"), planned_duration="14 days")
    recipe = RecipeFactory()
    copy_template_to_recipe(template, recipe)

    step.planned_duration = "30 days"
    step.save()

    assert recipe.plan_steps.get().planned_days == pytest.approx(14)


@pytest.mark.django_db
def test_copying_a_template_replaces_the_recipes_existing_plan():
    recipe = RecipeFactory()
    RecipePlanStepFactory(recipe=recipe, sort_order=1, stage=_stage("pitch"), planned_duration="3 days")
    template = WorkflowTemplateFactory()
    WorkflowTemplateStepFactory(template=template, sort_order=1, stage=_stage("pitch"), planned_duration="10 days")
    WorkflowTemplateStepFactory(template=template, sort_order=2, stage=_stage("racking"), planned_duration=None)

    copy_template_to_recipe(template, recipe)

    assert [(s.stage.shortid, s.planned_days) for s in recipe.plan_steps.all()] == [
        ("pitch", pytest.approx(10)), ("racking", None),
    ]


@pytest.mark.django_db
def test_a_recipe_can_have_no_plan():
    recipe = RecipeFactory()

    assert recipe.workflow_template is None
    assert not recipe.plan_steps.exists()
