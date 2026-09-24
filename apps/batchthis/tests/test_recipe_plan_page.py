"""Step 8b: a recipe's Edit plan page - TODO-BatchStage.txt step 8 DECISIONS."""
import html
import re

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from ..factories import RecipeFactory, WorkflowTemplateFactory
from ..models import BatchStage, PlanStep, Vessel
from ..services import clear_recipe_plan, save_recipe_plan, save_workflow_template

FERMENTER, AGING_TANK, BARREL, CURRENT = (
    Vessel.TYPE_FERMENTER, Vessel.TYPE_AGING_TANK, Vessel.TYPE_BARREL, PlanStep.VESSEL_CURRENT,
)
GOOD = [("pitch", "14 days", FERMENTER), ("racking", "30 days", AGING_TANK),
        ("sterile-filtering", None, AGING_TANK), ("complete-batch", None, CURRENT)]


def _stage(shortid):
    return BatchStage.objects.get(shortid=shortid)


def _rows(*rows):
    return [{"stage": _stage(s), "planned_duration": d, "vessel_type": v, "notes": ""} for s, d, v in rows]


def _steps(recipe):
    return [(s.sort_order, s.stage.shortid, s.planned_days, s.vessel_type) for s in recipe.plan_steps.all()]


def _template(name="Traditional mead", rows=GOOD):
    return save_workflow_template(None, name=name, description="", rows=_rows(*rows))


def _post_data(steps=GOOD, template=None):
    data = {"workflow_template": template.pk if template else "",
            "steps-TOTAL_FORMS": str(len(steps)), "steps-INITIAL_FORMS": "0",
            "steps-MIN_NUM_FORMS": "0", "steps-MAX_NUM_FORMS": "1000"}
    for i, (shortid, duration, vessel_type) in enumerate(steps):
        data.update({f"steps-{i}-stage": _stage(shortid).pk, f"steps-{i}-planned_duration": duration or "",
                     f"steps-{i}-vessel_type": vessel_type, f"steps-{i}-notes": ""})
    return data


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


def _url(recipe, **query):
    url = reverse("editRecipePlan", kwargs={"pk": recipe.pk})
    return url + ("?" + "&".join(f"{k}={v}" for k, v in query.items()) if query else "")


# ---------- services ----------

@pytest.mark.django_db
def test_save_recipe_plan_replaces_the_steps_in_order_and_records_the_template():
    recipe = RecipeFactory()
    template = _template()
    save_recipe_plan(recipe, _rows(("pitch", "3 days", FERMENTER)), template=None)

    save_recipe_plan(recipe, _rows(*GOOD), template=template)

    recipe.refresh_from_db()
    assert recipe.workflow_template == template
    assert _steps(recipe) == [(1, "pitch", 14, FERMENTER), (2, "racking", 30, AGING_TANK),
                              (3, "sterile-filtering", None, AGING_TANK), (4, "complete-batch", None, CURRENT)]


@pytest.mark.django_db
def test_save_recipe_plan_without_a_template_keeps_the_copied_from_reference():
    recipe = RecipeFactory()
    template = _template()
    save_recipe_plan(recipe, _rows(*GOOD), template=template)

    save_recipe_plan(recipe, _rows(("pitch", "2 days", FERMENTER)))

    recipe.refresh_from_db()
    assert recipe.workflow_template == template
    assert _steps(recipe) == [(1, "pitch", 2, FERMENTER)]


@pytest.mark.django_db
@pytest.mark.parametrize("rows, message", [
    ([], "Add at least one step."),
    ([("racking", "30 days", AGING_TANK)], "Step 1: the plan must start with Pitch."),
])
def test_save_recipe_plan_rejects_an_invalid_plan_and_keeps_the_old_one(rows, message):
    recipe = RecipeFactory()
    save_recipe_plan(recipe, _rows(*GOOD))

    with pytest.raises(ValidationError) as error:
        save_recipe_plan(recipe, _rows(*rows))

    assert message in error.value.messages
    assert len(_steps(recipe)) == 4


@pytest.mark.django_db
def test_clear_recipe_plan_removes_the_steps_and_the_reference():
    recipe = RecipeFactory()
    save_recipe_plan(recipe, _rows(*GOOD), template=_template())

    clear_recipe_plan(recipe)

    recipe.refresh_from_db()
    assert recipe.workflow_template is None and not recipe.plan_steps.exists()


# ---------- Opening the page ----------

@pytest.mark.django_db
def test_recipe_page_links_to_edit_plan(client):
    recipe = RecipeFactory()

    page = client.get(reverse("recipe", kwargs={"pk": recipe.pk})).content.decode()

    assert f'href="{_url(recipe)}"' in page


@pytest.mark.django_db
def test_a_recipe_without_a_plan_starts_with_the_shortest_valid_chain(client):
    formset = client.get(_url(RecipeFactory())).context["formset"]

    assert [f["stage"].value() for f in formset] == [
        _stage(s).pk for s in ("pitch", "racking", "sterile-filtering", "complete-batch")]


@pytest.mark.django_db
def test_a_recipe_with_a_plan_shows_its_own_steps_and_where_they_came_from(client):
    recipe = RecipeFactory()
    save_recipe_plan(recipe, _rows(("pitch", "5 days", FERMENTER), ("racking", None, BARREL)), template=_template())

    response = client.get(_url(recipe))

    formset = response.context["formset"]
    assert [(f["stage"].value(), f["vessel_type"].value()) for f in formset] == [
        (_stage("pitch").pk, FERMENTER), (_stage("racking").pk, BARREL)]
    assert "Copied from Traditional mead" in html.unescape(response.content.decode())


@pytest.mark.django_db
def test_the_page_offers_every_workflow_to_start_from(client):
    _template("Traditional mead")
    _template("Quick cyser")

    page = client.get(_url(RecipeFactory())).content.decode()

    picker = re.search(r'<select name="template"[^>]*>.*?</select>', page, re.DOTALL).group(0)
    assert "Traditional mead" in picker and "Quick cyser" in picker


# ---------- Starting from a workflow: pre-fill, nothing saved until Save ----------

@pytest.mark.django_db
def test_choosing_a_workflow_prefills_the_rows_but_saves_nothing(client):
    recipe = RecipeFactory()
    save_recipe_plan(recipe, _rows(("pitch", "5 days", FERMENTER)))
    template = _template()

    response = client.get(_url(recipe, template=template.pk))

    formset = response.context["formset"]
    assert [f["stage"].value() for f in formset] == [_stage(s).pk for s, _, _ in GOOD]
    assert response.context["form"]["workflow_template"].value() == template.pk
    recipe.refresh_from_db()
    assert recipe.workflow_template is None and _steps(recipe) == [(1, "pitch", 5, FERMENTER)]


@pytest.mark.django_db
def test_saving_records_the_chosen_workflow_and_returns_to_the_recipe(client):
    recipe = RecipeFactory()
    template = _template()

    response = client.post(_url(recipe), _post_data(template=template))

    assert response.status_code == 302 and response["Location"] == reverse("recipe", kwargs={"pk": recipe.pk})
    recipe.refresh_from_db()
    assert recipe.workflow_template == template
    assert [s[1] for s in _steps(recipe)] == ["pitch", "racking", "sterile-filtering", "complete-batch"]


@pytest.mark.django_db
def test_adjusted_rows_are_what_gets_saved(client):
    recipe = RecipeFactory()
    template = _template()

    client.post(_url(recipe), _post_data(steps=[("pitch", "21 days", FERMENTER), ("racking", None, BARREL)],
                                         template=template))

    assert _steps(recipe) == [(1, "pitch", 21, FERMENTER), (2, "racking", None, BARREL)]
    assert list(template.steps.values_list("sort_order", flat=True)) == [1, 2, 3, 4]  # template untouched


@pytest.mark.django_db
def test_an_invalid_plan_shows_row_errors_and_keeps_the_chosen_workflow(client):
    recipe = RecipeFactory()
    template = _template()

    response = client.post(_url(recipe), _post_data(
        steps=[("pitch", "14 days", FERMENTER), ("sterile-filtering", None, AGING_TANK)], template=template))

    assert response.status_code == 200
    assert response.context["formset"].forms[1].non_field_errors()
    assert f'name="workflow_template" value="{template.pk}"' in response.content.decode()
    assert not recipe.plan_steps.exists()


@pytest.mark.django_db
def test_an_unknown_workflow_is_a_404(client):
    recipe = RecipeFactory()

    assert client.get(_url(recipe, template=9999)).status_code == 404
    assert client.post(_url(recipe), _post_data(template=WorkflowTemplateFactory.build(pk=9999))).status_code == 200


# ---------- Clear plan ----------

@pytest.mark.django_db
def test_clear_plan_is_offered_only_when_there_is_a_plan(client):
    recipe = RecipeFactory()
    clear_url = reverse("clearRecipePlan", kwargs={"pk": recipe.pk})
    assert clear_url not in client.get(_url(recipe)).content.decode()

    save_recipe_plan(recipe, _rows(*GOOD))

    assert f'action="{clear_url}"' in client.get(_url(recipe)).content.decode()


@pytest.mark.django_db
def test_clearing_the_plan_removes_it_and_returns_to_the_recipe(client):
    recipe = RecipeFactory()
    save_recipe_plan(recipe, _rows(*GOOD), template=_template())

    response = client.post(reverse("clearRecipePlan", kwargs={"pk": recipe.pk}))

    assert response.status_code == 302 and response["Location"] == reverse("recipe", kwargs={"pk": recipe.pk})
    recipe.refresh_from_db()
    assert recipe.workflow_template is None and not recipe.plan_steps.exists()


@pytest.mark.django_db
def test_clear_plan_is_post_only(client):
    recipe = RecipeFactory()
    save_recipe_plan(recipe, _rows(*GOOD))

    assert client.get(reverse("clearRecipePlan", kwargs={"pk": recipe.pk})).status_code == 405
    assert recipe.plan_steps.count() == 4


# ---------- Access ----------

@pytest.mark.django_db
def test_plan_pages_require_login_and_404_for_a_missing_recipe(client):
    recipe = RecipeFactory()
    for url in (_url(recipe), reverse("clearRecipePlan", kwargs={"pk": recipe.pk})):
        response = Client().post(url) if "clear" in url.lower() else Client().get(url)
        assert response.status_code == 302 and "login" in response["Location"], url
    assert client.get(reverse("editRecipePlan", kwargs={"pk": 9999})).status_code == 404
    assert client.post(reverse("clearRecipePlan", kwargs={"pk": 9999})).status_code == 404
