"""
Login is required site-wide (Aaron, 2026-09-25) - django.contrib.auth's
LoginRequiredMiddleware. Before, only some views had @login_required: a logged-out
visitor could open Add batch, Add recipe and the recipe ingredient editors, and
LOGIN_URL was unset, so the decorated views redirected to a /accounts/login/
that doesn't exist.
"""
import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import get_resolver, reverse

from ..factories import BatchFactory


def _batchthis_paths() -> list[str]:
    """Every batchthis URL, with each <converter:name> filled in with a sample value."""
    resolver = get_resolver()
    batchthis = next(p for p in resolver.url_patterns if str(p.pattern) == "batchthis/")
    paths = []
    for pattern in batchthis.url_patterns:
        route = str(pattern.pattern)
        route = re.sub(r"<int:\w+>", "1", route)
        route = re.sub(r"<(?:str:)?\w+>", "x", route)
        paths.append("/batchthis/" + route)
    return paths


@pytest.mark.django_db
@pytest.mark.parametrize("path", [p for p in _batchthis_paths() if "/api/" not in p])
def test_every_batchthis_page_redirects_a_logged_out_visitor_to_login(path):
    response = Client().get(path)

    assert response.status_code == 302, path
    assert response["Location"].startswith(reverse("login") + "?next="), response["Location"]


@pytest.mark.django_db
@pytest.mark.parametrize("path", [p for p in _batchthis_paths() if "/api/" in p])
def test_the_api_refuses_a_logged_out_request_with_403_json(path):
    # DRF views are exempt from the middleware; their IsAuthenticated permission
    # answers with JSON instead of redirecting to the HTML login page.
    response = Client().get(path)

    assert response.status_code == 403, path
    assert response["Content-Type"] == "application/json"


@pytest.mark.django_db
def test_a_logged_out_post_saves_nothing():
    from ..models import Recipe
    before = Recipe.objects.count()

    response = Client().post(reverse("addRecipe"), {"name": "Anonymous recipe"})

    assert response.status_code == 302
    assert Recipe.objects.count() == before


@pytest.mark.django_db
def test_the_login_redirect_lands_on_a_working_login_page():
    response = Client().get(reverse("batchListing"), follow=True)

    assert response.status_code == 200
    assert response.redirect_chain[-1][0].startswith("/user/login/")
    assert "form" in response.context


@pytest.mark.django_db
def test_logging_in_opens_the_pages():
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    batch = BatchFactory()

    for url in (reverse("index"), reverse("batchListing"), reverse("batch", kwargs={"pk": batch.pk}),
                reverse("addBatch"), reverse("addRecipe")):
        assert client.get(url).status_code == 200, url
