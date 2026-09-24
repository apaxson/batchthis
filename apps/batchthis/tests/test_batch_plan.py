"""Step 10: a batch's own plan - copied at creation, editable until Pitch (TODO-BatchStage.txt)."""
import datetime
import html
import re

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from ..factories import BatchFactory, FermenterFactory, RecipeFactory, VesselFactory
from ..models import Batch, BatchPlanStep, BatchStage, PlanStep, Vessel
from ..services import (
    copy_plan_to_batch,
    save_batch_plan,
    save_recipe_plan,
    save_workflow_template,
    transition_stage_event,
)

FERMENTER, AGING_TANK, BARREL, CURRENT = (
    Vessel.TYPE_FERMENTER, Vessel.TYPE_AGING_TANK, Vessel.TYPE_BARREL, PlanStep.VESSEL_CURRENT,
)
RECIPE_PLAN = [("pitch", "14 days", FERMENTER), ("racking", "30 days", AGING_TANK),
               ("sterile-filtering", None, AGING_TANK), ("complete-batch", None, CURRENT)]
TEMPLATE_PLAN = [("pitch", "10 days", FERMENTER), ("racking", "60 days", BARREL)]


def _stage(shortid):
    return BatchStage.objects.get(shortid=shortid)


def _rows(*rows):
    return [{"stage": _stage(s), "planned_duration": d, "vessel_type": v, "notes": ""} for s, d, v in rows]


def _steps(owner):
    return [(s.sort_order, s.stage.shortid, s.planned_days, s.vessel_type) for s in owner.plan_steps.all()]


def _template(name="Traditional mead", rows=TEMPLATE_PLAN):
    return save_workflow_template(None, name=name, description="", rows=_rows(*rows))


def _recipe_with_plan(template=None):
    recipe = RecipeFactory()
    save_recipe_plan(recipe, _rows(*RECIPE_PLAN), template=template)
    return recipe


def _batch(recipe=None, template=None):
    """An unpitched batch with its plan copied the way Add batch does it."""
    batch = BatchFactory(recipe=recipe or _recipe_with_plan())
    copy_plan_to_batch(batch, template=template)
    return batch


def _pitch(batch):
    vessel = batch.fermenter.vessel
    vessel.status = Vessel.STATUS_ACTIVE
    vessel.save()
    batch.vessel = vessel
    batch.startdate = timezone.now() - datetime.timedelta(days=5)
    batch.save()
    transition_stage_event(batch, _stage("pitch"), timestamp=timezone.now() - datetime.timedelta(days=1))


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


# ---------- copy_plan_to_batch(): where a new batch's plan comes from ----------

@pytest.mark.django_db
def test_a_batch_copies_its_recipes_plan_and_copied_from():
    template = _template()
    recipe = _recipe_with_plan(template=template)

    batch = _batch(recipe)

    assert _steps(batch) == _steps(recipe)
    assert batch.workflow_template == template


@pytest.mark.django_db
def test_the_recipes_plan_wins_over_a_workflow_passed_in():
    recipe = _recipe_with_plan()

    batch = _batch(recipe, template=_template())

    assert _steps(batch) == _steps(recipe)
    assert batch.workflow_template is None


@pytest.mark.django_db
def test_a_recipe_without_a_plan_uses_the_chosen_workflow():
    template = _template()

    batch = _batch(RecipeFactory(), template=template)

    assert _steps(batch) == [(1, "pitch", 10, FERMENTER), (2, "racking", 60, BARREL)]
    assert batch.workflow_template == template


@pytest.mark.django_db
def test_a_recipe_without_a_plan_needs_a_workflow():
    batch = BatchFactory(recipe=RecipeFactory())

    with pytest.raises(ValidationError, match="choose a workflow"):
        copy_plan_to_batch(batch)

    assert not batch.plan_steps.exists()


@pytest.mark.django_db
def test_the_batch_keeps_its_own_copy():
    recipe = _recipe_with_plan()
    batch = _batch(recipe)

    save_recipe_plan(recipe, _rows(("pitch", "1 day", FERMENTER)))

    assert len(_steps(batch)) == 4


# ---------- save_batch_plan(): editable until Pitch ----------

@pytest.mark.django_db
def test_an_unpitched_batch_plan_can_be_changed():
    batch = _batch()

    save_batch_plan(batch, _rows(("pitch", "21 days", FERMENTER), ("racking", None, BARREL)))

    assert _steps(batch) == [(1, "pitch", 21, FERMENTER), (2, "racking", None, BARREL)]


@pytest.mark.django_db
def test_the_plan_is_locked_after_pitch():
    batch = _batch()
    _pitch(batch)

    with pytest.raises(ValidationError, match="after Pitch"):
        save_batch_plan(batch, _rows(("pitch", "21 days", FERMENTER)))

    assert len(_steps(batch)) == 4


@pytest.mark.django_db
def test_a_batch_without_a_plan_does_not_get_one_here():
    batch = BatchFactory(recipe=RecipeFactory())   # e.g. created before batch plans

    with pytest.raises(ValidationError, match="no plan"):
        save_batch_plan(batch, _rows(("pitch", "21 days", FERMENTER)))


@pytest.mark.django_db
def test_an_invalid_batch_plan_is_rejected():
    batch = _batch()

    with pytest.raises(ValidationError, match="must start with Pitch"):
        save_batch_plan(batch, _rows(("racking", "30 days", AGING_TANK)))

    assert len(_steps(batch)) == 4


# ---------- Add batch ----------

def _add_batch_data(recipe, **overrides):
    fermenter = FermenterFactory(vessel=VesselFactory(status=Vessel.STATUS_READY))
    data = {"name": "Orange blossom 1", "startdate": timezone.localdate().isoformat(), "size": "6 gallons",
            "fermenter": fermenter.pk, "startingGravity": "1.100", "estimatedEndGravity": "1.010",
            "recipe": recipe.pk, "workflow_template": ""}
    data.update(overrides)
    return data


@pytest.mark.django_db
def test_add_batch_copies_the_recipes_plan(client):
    recipe = _recipe_with_plan()

    response = client.post(reverse("addBatch"), _add_batch_data(recipe))

    batch = Batch.objects.get(name="Orange blossom 1")
    assert response.status_code == 302
    assert _steps(batch) == _steps(recipe)


@pytest.mark.django_db
def test_add_batch_uses_the_chosen_workflow_when_the_recipe_has_no_plan(client):
    template = _template()

    client.post(reverse("addBatch"), _add_batch_data(RecipeFactory(), workflow_template=template.pk))

    batch = Batch.objects.get(name="Orange blossom 1")
    assert batch.workflow_template == template and len(_steps(batch)) == 2


@pytest.mark.django_db
def test_add_batch_requires_a_workflow_when_the_recipe_has_no_plan(client):
    data = _add_batch_data(RecipeFactory())

    response = client.post(reverse("addBatch"), data)

    assert response.status_code == 200
    assert response.context["form"].errors["workflow_template"] == ["This recipe has no plan - choose a workflow."]
    assert not Batch.objects.exists()
    assert Vessel.objects.get(fermenter__pk=data["fermenter"]).status == Vessel.STATUS_READY


@pytest.mark.django_db
def test_add_batch_page_offers_workflows_and_knows_which_recipes_have_plans(client):
    _template("Traditional mead")
    with_plan, without_plan = _recipe_with_plan(), RecipeFactory()

    page = client.get(reverse("addBatch")).content.decode()

    picker = re.search(r'<select name="workflow_template"[^>]*>.*?</select>', page, re.DOTALL).group(0)
    assert "Traditional mead" in picker
    planned = re.search(r'data-recipes-with-plans="([^"]*)"', page).group(1).split(",")
    assert str(with_plan.pk) in planned and str(without_plan.pk) not in planned


# ---------- Batch page ----------

def _batch_page(client, batch):
    return html.unescape(client.get(reverse("batch", kwargs={"pk": batch.pk})).content.decode())


def _plan_section(page):
    match = re.search(r'<section class="cl-plan"[^>]*>.*?</section>', page, re.DOTALL)
    return match.group(0) if match else None


@pytest.mark.django_db
def test_batch_page_shows_the_plan_and_offers_edit_before_pitch(client):
    template = _template()
    batch = _batch(_recipe_with_plan(template=template))

    page = _batch_page(client, batch)

    section = _plan_section(page)
    edit_url = reverse("editBatchPlan", kwargs={"pk": batch.pk})
    assert "Racking" in section and "Copied from Traditional mead" in section
    assert f'href="{edit_url}"' in section
    assert re.search(rf'<a role="menuitem" href="{re.escape(edit_url)}"', page)


@pytest.mark.django_db
def test_batch_page_plan_is_read_only_after_pitch(client):
    batch = _batch()
    _pitch(batch)

    page = _batch_page(client, batch)

    edit_url = reverse("editBatchPlan", kwargs={"pk": batch.pk})
    assert "Locked after Pitch" in _plan_section(page)
    assert edit_url not in page


@pytest.mark.django_db
def test_a_batch_without_a_plan_shows_no_plan_section(client):
    batch = BatchFactory(recipe=RecipeFactory())

    page = _batch_page(client, batch)

    assert _plan_section(page) is None
    assert reverse("editBatchPlan", kwargs={"pk": batch.pk}) not in page


# ---------- The batch's Edit plan page ----------

def _post_steps(steps):
    data = {"steps-TOTAL_FORMS": str(len(steps)), "steps-INITIAL_FORMS": "0",
            "steps-MIN_NUM_FORMS": "0", "steps-MAX_NUM_FORMS": "1000"}
    for i, (shortid, duration, vessel_type) in enumerate(steps):
        data.update({f"steps-{i}-stage": _stage(shortid).pk, f"steps-{i}-planned_duration": duration or "",
                     f"steps-{i}-vessel_type": vessel_type, f"steps-{i}-notes": ""})
    return data


@pytest.mark.django_db
def test_edit_plan_page_prefills_the_batchs_steps(client):
    batch = _batch()

    formset = client.get(reverse("editBatchPlan", kwargs={"pk": batch.pk})).context["formset"]

    assert [f["stage"].value() for f in formset] == [_stage(s).pk for s, _, _ in RECIPE_PLAN]


@pytest.mark.django_db
def test_saving_the_batch_plan_returns_to_the_batch(client):
    batch = _batch()

    response = client.post(reverse("editBatchPlan", kwargs={"pk": batch.pk}),
                           _post_steps([("pitch", "21 days", FERMENTER), ("racking", None, BARREL)]))

    assert response.status_code == 302 and response["Location"] == reverse("batch", kwargs={"pk": batch.pk})
    assert _steps(batch) == [(1, "pitch", 21, FERMENTER), (2, "racking", None, BARREL)]


@pytest.mark.django_db
def test_an_invalid_batch_plan_shows_row_errors(client):
    batch = _batch()

    response = client.post(reverse("editBatchPlan", kwargs={"pk": batch.pk}),
                           _post_steps([("pitch", "14 days", FERMENTER), ("sterile-filtering", None, AGING_TANK)]))

    assert response.status_code == 200
    assert response.context["formset"].forms[1].non_field_errors()
    assert len(_steps(batch)) == 4


@pytest.mark.django_db
def test_edit_plan_page_is_read_only_after_pitch_and_saves_nothing(client):
    batch = _batch()
    _pitch(batch)
    url = reverse("editBatchPlan", kwargs={"pk": batch.pk})

    page = html.unescape(client.get(url).content.decode())
    assert "Locked after Pitch" in page and 'id="batchplanform"' not in page
    assert "Racking" in page   # the plan still shows, read-only

    response = client.post(url, _post_steps([("pitch", "21 days", FERMENTER)]))
    assert response.status_code == 200
    assert "can't be changed after Pitch" in html.unescape(response.content.decode())
    assert len(_steps(batch)) == 4


@pytest.mark.django_db
def test_edit_plan_pages_require_login_and_404(client):
    batch = _batch()
    response = Client().get(reverse("editBatchPlan", kwargs={"pk": batch.pk}))
    assert response.status_code == 302 and "login" in response["Location"]
    assert client.get(reverse("editBatchPlan", kwargs={"pk": 9999})).status_code == 404


@pytest.mark.django_db
def test_batch_plan_steps_are_their_own_rows():
    batch = _batch()

    assert BatchPlanStep.objects.filter(batch=batch).count() == 4
    assert issubclass(BatchPlanStep, PlanStep)
