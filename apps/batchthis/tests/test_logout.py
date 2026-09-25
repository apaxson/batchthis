"""
Log out from the sidebar's Settings group (Aaron, 2026-09-25). It opens the shared
#logoutModal in base.html, whose Logout button POSTs to the auth logout view -
Django 5+ only logs out on POST, so the old <a href> Logout link got a 405.
"""
import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


def _page(client) -> str:
    return client.get(reverse("index")).content.decode()


def test_the_settings_group_has_a_log_out_item_that_opens_the_logout_dialog(client):
    page = _page(client)

    settings = page[page.index('<div class="cl-nav-heading">Settings</div>'):page.index("</nav>")]
    assert re.search(r'<button[^>]*data-toggle="modal"[^>]*data-target="#logoutModal"', settings)
    assert '<span class="nav-label">Log out</span>' in settings


def test_the_page_has_exactly_one_logout_dialog(client):
    assert _page(client).count('id="logoutModal"') == 1


def test_the_logout_dialog_posts_to_logout_with_a_csrf_token(client):
    page = _page(client)

    dialog = page[page.index('id="logoutModal"'):]
    form = re.search(r'<form method="post" action="([^"]+)">(.*?)</form>', dialog, re.S)
    assert form and form.group(1) == reverse("logout")
    assert 'name="csrfmiddlewaretoken"' in form.group(2)
    assert f'href="{reverse("logout")}"' not in page


def test_posting_to_logout_ends_the_session_and_goes_to_login(client):
    response = client.post(reverse("logout"))

    assert response.status_code == 302
    assert response["Location"] == reverse("login")
    assert client.get(reverse("index")).status_code == 302   # logged out: sent back to login


def test_a_logged_out_visitor_sees_no_sidebar_links():
    page = Client().get(reverse("login")).content.decode()

    assert "Log out" not in page
