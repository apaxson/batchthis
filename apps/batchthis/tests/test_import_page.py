"""
The BeerSmith import forms moved from admin/import to their own Cellar Ledger page
(batchthis/import), reached from the sidebar's Tools > Import.
"""
import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from ..models import Yeast


YEAST_XML = """<?xml version="1.0" encoding="ISO-8859-1"?>
<YEASTS>
{}
</YEASTS>"""

YEAST = """<YEAST>
 <NAME>{name}</NAME><VERSION>1</VERSION><TYPE>Wine</TYPE><FORM>Dry</FORM>
 <AMOUNT>0.005</AMOUNT><AMOUNT_IS_WEIGHT>TRUE</AMOUNT_IS_WEIGHT>
 <ATTENUATION>80</ATTENUATION><FLOCCULATION>Low</FLOCCULATION>
 <MIN_TEMPERATURE>10</MIN_TEMPERATURE><MAX_TEMPERATURE>30</MAX_TEMPERATURE>
 <NOTES>Test yeast</NOTES>
</YEAST>"""


@pytest.fixture
def client(db):
    user = get_user_model().objects.create_user(username="cellarhand", password="password123")
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)


def _upload(xml: str, field: str = "misc_upload") -> dict:
    return {field: SimpleUploadedFile("export.xml", xml.encode("iso-8859-1"), content_type="text/xml")}


@pytest.mark.django_db
def test_import_page_shows_both_upload_forms_in_the_cellar_ledger_look(client):
    response = client.get(reverse("importData"))

    page = response.content.decode()
    assert response.status_code == 200
    assert 'name="recipe_upload"' in page and 'name="misc_upload"' in page
    assert page.count('enctype="multipart/form-data"') == 2
    assert "cl-panel" in page
    assert "crispy" not in page and "topbar" not in page


@pytest.mark.django_db
def test_old_admin_import_url_redirects_to_the_new_page(client):
    response = client.get("/batchthis/admin/import")

    assert response.status_code in (301, 302)
    assert response["Location"] == reverse("importData")


@pytest.mark.django_db
def test_import_without_a_file_shows_an_error(client):
    response = client.post(reverse("importData"))

    assert any("no file" in str(m).lower() for m in response.context["messages"])


@pytest.mark.django_db
def test_misc_import_creates_new_yeasts_and_skips_existing_ones(client):
    xml = YEAST_XML.format(YEAST.format(name="Lalvin EC-1118") + YEAST.format(name="Lalvin 71B"))
    client.post(reverse("importData"), _upload(xml))

    response = client.post(reverse("importData"), _upload(xml))

    assert Yeast.objects.filter(name__in=["Lalvin EC-1118", "Lalvin 71B"]).count() == 2
    summary = " ".join(str(m) for m in response.context["messages"])
    assert "Yeasts: 0 created, 2 skipped" in summary


@pytest.mark.django_db
def test_misc_import_of_a_single_item_export_works(client):
    # xmltodict gives a lone <YEAST> as a dict, not a one-item list.
    client.post(reverse("importData"), _upload(YEAST_XML.format(YEAST.format(name="Lalvin D47"))))

    assert Yeast.objects.filter(name="Lalvin D47").exists()


@pytest.mark.django_db
def test_an_unreadable_file_shows_an_error_instead_of_crashing(client):
    response = client.post(reverse("importData"), _upload("this is not xml"))

    assert response.status_code == 200
    assert any("couldn't import" in str(m).lower() for m in response.context["messages"])


@pytest.mark.django_db
def test_sidebar_tools_is_a_heading_with_utils_and_import_links(client):
    page = client.get(reverse("index")).content.decode()

    assert f'href="{reverse("refractometerCorrection")}"' in page
    assert f'href="{reverse("importData")}"' in page
    assert ">Utils<" in page and ">Import<" in page
    tools = page.index(">Tools<")
    # "Tools" is no longer itself a link.
    assert page.rfind("<a ", 0, tools) < page.rfind("nav-parent", 0, tools)
