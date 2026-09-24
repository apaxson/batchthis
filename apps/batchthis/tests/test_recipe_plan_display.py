"""Step 9: show a recipe's plan on its page - step chain + total planned time."""
import html
import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from ..factories import RecipeFactory
from ..models import BatchStage, PlanStep, Vessel
from ..services import save_recipe_plan, save_workflow_template

FERMENTER, AGING_TANK, BARREL, CURRENT = (
    Vessel.TYPE_FERMENTER, Vessel.TYPE_AGING_TANK, Vessel.TYPE_BARREL, PlanStep.VESSEL_CURRENT,
)
BOTTLES = PlanStep.VESSEL_BOTTLES


def _rows(*rows):
    return [{"stage": BatchStage.objects.get(shortid=s), "planned_duration": d, "vessel_type": v, "notes": n}
            for s, d, v, n in rows]


PLAN = [("pitch", "14 days", FERMENTER, "Keep at 62F"), ("racking", "30 days", AGING_TANK, ""),
        ("fine-filtering", None, BARREL, ""), ("sterile-filtering", "5 days", BOTTLES, ""),
        ("complete-batch", None, CURRENT, "")]


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


def _plan_section(client, recipe):
    page = html.unescape(client.get(reverse("recipe", kwargs={"pk": recipe.pk})).content.decode())
    return re.search(r'<section class="cl-plan"[^>]*>.*?</section>', page, re.DOTALL).group(0)


def _cells(section):
    """Body rows as lists of their cell text."""
    body = re.search(r"<tbody>(.*?)</tbody>", section, re.DOTALL).group(1)
    return [[re.sub(r"<[^>]+>", "", cell).strip() for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)]
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.DOTALL)]


@pytest.mark.django_db
def test_recipe_page_lists_each_plan_step_in_order(client):
    recipe = RecipeFactory()
    save_recipe_plan(recipe, _rows(*PLAN))

    rows = _cells(_plan_section(client, recipe))

    assert [row[:3] for row in rows] == [
        ["1", "Pitch", "Fermenter"], ["2", "Racking", "Aging Tank"], ["3", "Fine Filtering", "Barrel"],
        ["4", "Sterile Filtering", "Bottles"], ["5", "Complete Batch", "None / Current"],
    ]
    assert rows[0][3] == "14.00 day" and rows[0][4] == "Keep at 62F"
    assert rows[2][3] == "—" and rows[4][3] == "—"   # no planned time


@pytest.mark.django_db
def test_recipe_page_totals_only_the_timed_steps_by_state(client):
    recipe = RecipeFactory()
    save_recipe_plan(recipe, _rows(*PLAN))

    footer = re.search(r"<tfoot>(.*?)</tfoot>", _plan_section(client, recipe), re.DOTALL).group(1)
    text = " ".join(re.sub(r"<[^>]+>", " ", footer).split())

    assert "49 days" in text                                          # 14 + 30 + 5
    assert "Fermentation 14 days" in text and "Aging 30 days" in text and "Bottling 5 days" in text


@pytest.mark.django_db
def test_a_plan_with_no_durations_says_so(client):
    recipe = RecipeFactory()
    save_recipe_plan(recipe, _rows(("pitch", None, FERMENTER, "")))

    assert "No planned time" in _plan_section(client, recipe)


@pytest.mark.django_db
def test_recipe_page_shows_where_the_plan_came_from_and_links_to_edit_it(client):
    recipe = RecipeFactory()
    template = save_workflow_template(None, name="Traditional mead", description="", rows=_rows(*PLAN))
    save_recipe_plan(recipe, _rows(*PLAN), template=template)

    section = _plan_section(client, recipe)

    assert "Copied from Traditional mead" in section
    assert f'href="{reverse("editRecipePlan", kwargs={"pk": recipe.pk})}"' in section


@pytest.mark.django_db
def test_a_recipe_without_a_plan_invites_adding_one(client):
    recipe = RecipeFactory()

    section = _plan_section(client, recipe)

    assert "No plan yet" in section
    assert "<tfoot>" not in section
    assert f'href="{reverse("editRecipePlan", kwargs={"pk": recipe.pk})}"' in section
