"""Recipe page: each ingredient table's actions are in a kebab menu (Aaron, 2026-10-05), not a link row."""
import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from ..factories import RecipeFactory


@pytest.fixture
def page(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    recipe = RecipeFactory()
    return recipe, client.get(reverse("recipe", kwargs={"pk": recipe.pk})).content.decode()


def _menu(html, section):
    """The menu items (label, href) in the section head titled `section`."""
    head = re.search(r'<div class="cl-section-title">' + section + r'</div>(.*?)</ul>', html, re.DOTALL).group(1)
    assert 'data-cl-menu' in head and 'cl-menu--kebab' in head
    return re.findall(r'<a role="menuitem" href="([^"]+)"[^>]*>([^<]+)</a>', head)


@pytest.mark.django_db
def test_each_ingredient_table_has_a_kebab_menu_of_its_actions(page):
    recipe, html = page

    assert _menu(html, "Fermentables") == [(reverse("editFermentables", kwargs={"pk": recipe.pk}), "Add/edit fermentables")]
    assert _menu(html, "Adjuncts") == [(reverse("tosnaCalculator"), "TOSNA nutrients&hellip;"),
                                       (reverse("editAdjuncts", kwargs={"pk": recipe.pk}), "Add/edit adjuncts")]
    assert _menu(html, "Yeast") == [(reverse("editYeasts", kwargs={"pk": recipe.pk}), "Add/edit yeast")]


@pytest.mark.django_db
def test_the_tosna_item_still_opens_the_pop_up(page):
    recipe, html = page

    assert re.search(r'<a role="menuitem" href="[^"]+" data-cl-tosna data-recipe-url="'
                     + re.escape(reverse("recipe-tosna", kwargs={"pk": recipe.pk})) + '"', html)


@pytest.mark.django_db
def test_the_old_link_rows_are_gone(page):
    _recipe, html = page

    for section in ("Fermentables", "Adjuncts", "Yeast"):
        head = re.search(r'<div class="cl-section-title">' + section + r'</div>\s*(<div[^>]*>)', html).group(1)
        assert "cl-section-note" not in head


@pytest.mark.django_db
def test_the_plan_section_has_a_kebab_menu_and_keeps_its_copied_from_note(db):
    from ..factories import WorkflowTemplateFactory

    client = Client()
    client.force_login(get_user_model().objects.create_user(username="planner", password="pw"))
    recipe = RecipeFactory(workflow_template=WorkflowTemplateFactory(name="Traditional mead"))
    html = client.get(reverse("recipe", kwargs={"pk": recipe.pk})).content.decode()

    assert _menu(html, "Plan") == [(reverse("editRecipePlan", kwargs={"pk": recipe.pk}), "Edit plan")]
    plan_head = re.search(r'<div class="cl-section-title">Plan</div>(.*?)</ul>', html, re.DOTALL).group(1)
    assert "Copied from Traditional mead" in plan_head
