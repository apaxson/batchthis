"""Step 8a: Workflow templates pages (Settings > Workflows) - TODO-BatchStage.txt step 8 DECISIONS."""
import html
import json
import re

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from ..factories import (
    RecipeFactory,
    WorkflowTemplateFactory,
    WorkflowTemplateStepFactory,
)
from ..forms import PlanStepFormSet
from ..models import BatchStage, PlanStep, Vessel, WorkflowTemplate
from ..services import (
    allowed_vessel_types,
    copy_template_to_recipe,
    plan_step_problems,
    save_workflow_template,
)

FERMENTER, AGING_TANK, BARREL, CURRENT = (
    Vessel.TYPE_FERMENTER, Vessel.TYPE_AGING_TANK, Vessel.TYPE_BARREL, PlanStep.VESSEL_CURRENT,
)


def _stage(shortid):
    return BatchStage.objects.get(shortid=shortid)


def _rows(*rows):
    """(shortid, duration, vessel type[, notes]) -> save_workflow_template() rows."""
    return [
        {"stage": _stage(r[0]), "planned_duration": r[1], "vessel_type": r[2], "notes": r[3] if len(r) > 3 else ""}
        for r in rows
    ]


GOOD = [("pitch", "14 days", FERMENTER), ("racking", "30 days", AGING_TANK),
        ("sterile-filtering", None, AGING_TANK), ("complete-batch", None, CURRENT)]


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


def _post_data(name="Traditional mead", description="", steps=GOOD, deleted=()):
    """The page's POST: template fields + the 'steps' formset, rows in page order."""
    data = {"name": name, "description": description,
            "steps-TOTAL_FORMS": str(len(steps)), "steps-INITIAL_FORMS": "0",
            "steps-MIN_NUM_FORMS": "0", "steps-MAX_NUM_FORMS": "1000"}
    for i, row in enumerate(steps):
        shortid, duration, vessel_type = row[:3]
        data.update({
            f"steps-{i}-stage": _stage(shortid).pk if shortid else "",
            f"steps-{i}-planned_duration": duration or "",
            f"steps-{i}-vessel_type": vessel_type or "",
            f"steps-{i}-notes": row[3] if len(row) > 3 else "",
        })
        if i in deleted:
            data[f"steps-{i}-DELETE"] = "on"
    return data


def _steps(template):
    return [(s.sort_order, s.stage.shortid, s.planned_days, s.vessel_type) for s in template.steps.all()]


# ---------- services ----------

@pytest.mark.django_db
def test_plan_step_problems_points_each_message_at_its_step():
    steps = [(_stage("pitch"), "14 days", BARREL), (_stage("sterile-filtering"), None, CURRENT)]

    assert plan_step_problems(steps) == [
        (1, "Pitch always starts in a Fermenter."),
        (2, ("Sterile Filtering can't come after Pitch - the batch would be in Fermentation, "
             "and Sterile Filtering needs Aging.")),
        (2, "Sterile Filtering moves the batch - choose Fermenter, Aging Tank or Barrel."),
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("shortid, allowed", [
    ("pitch", [FERMENTER]),
    ("racking", [FERMENTER, AGING_TANK, BARREL]),
    ("fine-filtering", [FERMENTER, AGING_TANK, BARREL]),
    ("complete-batch", [CURRENT]),
])
def test_allowed_vessel_types_per_stage(shortid, allowed):
    assert allowed_vessel_types(_stage(shortid)) == allowed


@pytest.mark.django_db
def test_save_workflow_template_creates_it_with_its_steps_in_order():
    template = save_workflow_template(None, name="Traditional mead", description="Slow and cold",
                                      rows=_rows(*GOOD))

    assert (template.name, template.description) == ("Traditional mead", "Slow and cold")
    assert _steps(template) == [(1, "pitch", 14, FERMENTER), (2, "racking", 30, AGING_TANK),
                                (3, "sterile-filtering", None, AGING_TANK), (4, "complete-batch", None, CURRENT)]


@pytest.mark.django_db
def test_save_workflow_template_replaces_the_steps_of_an_existing_template():
    template = WorkflowTemplateFactory(name="Old")
    WorkflowTemplateStepFactory(template=template, stage=_stage("pitch"))

    save_workflow_template(template, name="New", description="",
                           rows=_rows(("pitch", "7 days", FERMENTER), ("racking", None, BARREL)))

    template.refresh_from_db()
    assert template.name == "New"
    assert _steps(template) == [(1, "pitch", 7, FERMENTER), (2, "racking", None, BARREL)]


@pytest.mark.django_db
@pytest.mark.parametrize("rows, message", [
    ([], "Add at least one step."),
    ([("racking", "30 days", AGING_TANK)], "Step 1: the plan must start with Pitch."),
])
def test_save_workflow_template_rejects_an_invalid_plan_and_saves_nothing(rows, message):
    template = WorkflowTemplateFactory(name="Keep me")
    WorkflowTemplateStepFactory(template=template, stage=_stage("pitch"))

    with pytest.raises(ValidationError) as error:
        save_workflow_template(template, name="Changed", description="", rows=_rows(*rows))

    assert message in error.value.messages
    template.refresh_from_db()
    assert template.name == "Keep me" and template.steps.count() == 1


# ---------- the shared step formset ----------

@pytest.mark.django_db
def test_formset_rows_come_back_in_page_order_skipping_blank_and_removed_rows():
    steps = [("pitch", "14 days", FERMENTER), (None, None, None), ("racking", "1 day", BARREL),
             ("sterile-filtering", None, AGING_TANK)]
    formset = PlanStepFormSet(_post_data(steps=steps), prefix="steps")

    assert formset.is_valid(), formset.errors
    assert [r["stage"].shortid for r in formset.plan_rows()] == ["pitch", "racking", "sterile-filtering"]

    formset = PlanStepFormSet(_post_data(steps=steps, deleted=(3,)), prefix="steps")
    assert formset.is_valid(), formset.errors
    assert [r["stage"].shortid for r in formset.plan_rows()] == ["pitch", "racking"]


@pytest.mark.django_db
def test_formset_puts_plan_problems_on_their_own_row():
    steps = [("pitch", "14 days", BARREL), ("sterile-filtering", None, CURRENT)]
    formset = PlanStepFormSet(_post_data(steps=steps), prefix="steps")

    assert not formset.is_valid()
    assert formset.forms[0].non_field_errors() == ["Pitch always starts in a Fermenter."]
    assert formset.forms[1].non_field_errors() == [
        ("Sterile Filtering can't come after Pitch - the batch would be in Fermentation, "
         "and Sterile Filtering needs Aging."),
        "Sterile Filtering moves the batch - choose Fermenter, Aging Tank or Barrel.",
    ]


@pytest.mark.django_db
def test_formset_with_no_steps_asks_for_one():
    formset = PlanStepFormSet(_post_data(steps=[]), prefix="steps")

    assert not formset.is_valid()
    assert formset.non_form_errors() == ["Add at least one step."]


@pytest.mark.django_db
def test_formset_row_errors_for_missing_fields():
    formset = PlanStepFormSet(_post_data(steps=[("pitch", "14", "")]), prefix="steps")

    assert not formset.is_valid()
    errors = formset.forms[0].errors
    assert errors["planned_duration"] == ["Units are required."]
    assert errors["vessel_type"] == ["This field is required."]


# ---------- Workflows list ----------

@pytest.mark.django_db
def test_sidebar_has_a_settings_group_with_workflows(client):
    page = client.get(reverse("index")).content.decode()

    assert "Settings" in page
    assert f'href="{reverse("workflowListing")}"' in page


@pytest.mark.django_db
def test_workflows_list_shows_each_template_with_steps_planned_time_and_recipes(client):
    template = save_workflow_template(None, name="Traditional mead", description="Slow and cold", rows=_rows(*GOOD))
    copy_template_to_recipe(template, RecipeFactory())

    page = client.get(reverse("workflowListing")).content.decode()

    assert reverse("addWorkflow") in page
    assert f'href="{reverse("editWorkflow", kwargs={"pk": template.pk})}"' in page
    row = re.search(r"<tr[^>]*>(?:(?!</tr>).)*Traditional mead.*?</tr>", page, re.DOTALL).group(0)
    assert "Slow and cold" in row
    assert re.search(r">\s*4\s*<", row)            # steps
    assert "44 days" in row                        # 14 + 30; untimed steps don't count
    assert re.search(r">\s*1\s*<", row)            # recipes that copied it


@pytest.mark.django_db
def test_workflows_list_empty_state_invites_adding_one(client):
    page = client.get(reverse("workflowListing")).content.decode()

    assert "No workflows yet" in page


# ---------- Add ----------

@pytest.mark.django_db
def test_add_page_starts_with_the_shortest_valid_chain(client):
    # Pitch -> Racking -> Sterile Filtering -> Complete Batch; the vessel types
    # Pitch and Complete Batch allow are pre-set, the others are left to choose.
    formset = client.get(reverse("addWorkflow")).context["formset"]

    assert [(f["stage"].value(), f["vessel_type"].value()) for f in formset] == [
        (_stage("pitch").pk, FERMENTER), (_stage("racking").pk, None),
        (_stage("sterile-filtering").pk, None), (_stage("complete-batch").pk, CURRENT),
    ]


@pytest.mark.django_db
def test_add_page_hands_the_vessel_type_rules_to_the_step_editor(client):
    page = client.get(reverse("addWorkflow")).content.decode()

    rules = json.loads(re.search(r"data-stage-rules='([^']*)'", page).group(1))
    assert rules[str(_stage("pitch").pk)] == {"vessel_types": [FERMENTER], "duration": True}
    assert rules[str(_stage("complete-batch").pk)] == {"vessel_types": [CURRENT], "duration": False}
    assert rules[str(_stage("racking").pk)]["vessel_types"] == [FERMENTER, AGING_TANK, BARREL]


@pytest.mark.django_db
def test_adding_a_workflow_saves_it_and_returns_to_the_list(client):
    response = client.post(reverse("addWorkflow"), _post_data())

    assert response.status_code == 302 and response["Location"] == reverse("workflowListing")
    template = WorkflowTemplate.objects.get(name="Traditional mead")
    assert [s[1] for s in _steps(template)] == ["pitch", "racking", "sterile-filtering", "complete-batch"]


@pytest.mark.django_db
def test_an_invalid_plan_shows_the_error_on_its_row_and_saves_nothing(client):
    steps = [("pitch", "14 days", FERMENTER), ("sterile-filtering", None, AGING_TANK)]

    response = client.post(reverse("addWorkflow"), _post_data(steps=steps))

    assert response.status_code == 200
    assert "Sterile Filtering can't come after Pitch" in html.unescape(response.content.decode())
    assert response.context["formset"].forms[1].non_field_errors()
    assert not WorkflowTemplate.objects.exists()


@pytest.mark.django_db
def test_workflow_names_are_unique_ignoring_case(client):
    WorkflowTemplateFactory(name="Traditional mead")

    response = client.post(reverse("addWorkflow"), _post_data(name="TRADITIONAL MEAD"))

    assert response.context["form"].errors["name"] == ["A workflow named 'Traditional mead' already exists."]
    assert WorkflowTemplate.objects.count() == 1


# ---------- Edit ----------

@pytest.mark.django_db
def test_edit_page_prefills_the_template_and_its_steps(client):
    template = save_workflow_template(None, name="Traditional mead", description="", rows=_rows(*GOOD))

    response = client.get(reverse("editWorkflow", kwargs={"pk": template.pk}))

    assert response.context["form"]["name"].value() == "Traditional mead"
    formset = response.context["formset"]
    assert [f["stage"].value() for f in formset] == [_stage(s).pk for s, _, _ in GOOD]
    assert str(formset.forms[1]["planned_duration"].value()) == "30.00 day"
    assert reverse("deleteWorkflow", kwargs={"pk": template.pk}) in response.content.decode()


@pytest.mark.django_db
def test_editing_a_workflow_keeps_the_name_and_replaces_the_steps(client):
    template = save_workflow_template(None, name="Traditional mead", description="", rows=_rows(*GOOD))

    response = client.post(reverse("editWorkflow", kwargs={"pk": template.pk}), _post_data(
        name="traditional mead", steps=[("pitch", "2 weeks", FERMENTER), ("racking", None, BARREL)]))

    assert response.status_code == 302
    template.refresh_from_db()
    assert template.name == "traditional mead"
    assert _steps(template) == [(1, "pitch", 14, FERMENTER), (2, "racking", None, BARREL)]


@pytest.mark.django_db
def test_editing_a_workflow_does_not_change_recipes_that_copied_it(client):
    template = save_workflow_template(None, name="Traditional mead", description="", rows=_rows(*GOOD))
    recipe = RecipeFactory()
    copy_template_to_recipe(template, recipe)

    client.post(reverse("editWorkflow", kwargs={"pk": template.pk}),
                _post_data(steps=[("pitch", "2 days", FERMENTER)]))

    assert recipe.plan_steps.count() == 4


# ---------- Delete (with confirmation) ----------

@pytest.mark.django_db
def test_delete_asks_first_and_says_recipes_keep_their_steps(client):
    template = save_workflow_template(None, name="Traditional mead", description="", rows=_rows(*GOOD))
    copy_template_to_recipe(template, RecipeFactory(name="Orange blossom"))

    response = client.get(reverse("deleteWorkflow", kwargs={"pk": template.pk}))

    page = response.content.decode()
    assert response.status_code == 200 and WorkflowTemplate.objects.exists()
    assert "Orange blossom" in page and "keep" in page
    assert f'action="{reverse("deleteWorkflow", kwargs={"pk": template.pk})}"' in page


@pytest.mark.django_db
def test_confirming_delete_removes_the_template_but_recipes_keep_their_steps(client):
    template = save_workflow_template(None, name="Traditional mead", description="", rows=_rows(*GOOD))
    recipe = RecipeFactory()
    copy_template_to_recipe(template, recipe)

    response = client.post(reverse("deleteWorkflow", kwargs={"pk": template.pk}), follow=True)

    assert not WorkflowTemplate.objects.exists()
    recipe.refresh_from_db()
    assert recipe.workflow_template is None and recipe.plan_steps.count() == 4
    page = html.unescape(response.content.decode())
    assert re.search(r'class="cl-flag cl-flag--ok"[^>]*>\s*<span>Deleted workflow \'Traditional mead\'.</span>', page)


# ---------- Access ----------

@pytest.mark.django_db
def test_workflow_pages_require_login_and_404_for_a_missing_template(client):
    pk = WorkflowTemplateFactory().pk
    for url in (reverse("workflowListing"), reverse("addWorkflow"),
                reverse("editWorkflow", kwargs={"pk": pk}), reverse("deleteWorkflow", kwargs={"pk": pk})):
        response = Client().get(url)
        assert response.status_code == 302 and "login" in response["Location"], url
    assert client.get(reverse("editWorkflow", kwargs={"pk": 9999})).status_code == 404
    assert client.post(reverse("deleteWorkflow", kwargs={"pk": 9999})).status_code == 404
