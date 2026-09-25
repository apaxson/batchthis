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


# ---------- Recipe import: small amounts come in in a smaller unit ----------

from pathlib import Path

from pint import Quantity

from ..factories import AdjunctUsageFactory
from ..models import Recipe
from ..views.admin import import_quantity

TART_CIDER = Path(__file__).resolve().parents[1] / "examples" / "tartcider.xml"


@pytest.mark.parametrize("amount, units, expected", [
    (0.001355, "liter", Quantity(1.355, "milliliter")),       # 0.00 L -> 1.36 mL
    (0.00142, "kilogram", Quantity(1.42, "gram")),            # 0.00 kg -> 1.42 g
    (0.000002, "kilogram", Quantity(2, "milligram")),         # steps down twice
    (0.007393, "liter", Quantity(0.007393, "liter")),         # shows as 0.01 L - not zero, stays
    (19.8166808, "liter", Quantity(19.8166808, "liter")),
    (0, "kilogram", Quantity(0, "kilogram")),                 # a real zero stays zero
    ("0.00142", "kilogram", Quantity(1.42, "gram")),          # BeerSmith gives strings
])
def test_import_quantity_steps_down_only_when_it_would_show_as_zero(amount, units, expected):
    result = import_quantity(amount, units)

    assert result.units == expected.units
    assert result.magnitude == pytest.approx(expected.magnitude)


def _import_tart_cider(client, with_primary=True, **replace):
    xml = TART_CIDER.read_text()
    for old, new in replace.items():
        xml = xml.replace(old, new)
    if with_primary:
        AdjunctUsageFactory(name="Primary")
    return client.post(reverse("importData"), {"recipe_upload": SimpleUploadedFile("tartcider.xml", xml.encode())})


@pytest.mark.django_db
def test_recipe_import_stores_a_tiny_adjunct_amount_in_a_smaller_unit(client):
    _import_tart_cider(client, **{"<AMOUNT>0.007393</AMOUNT>": "<AMOUNT>0.001355</AMOUNT>"})

    adjunct = Recipe.objects.get().adjuncts.get()
    assert str(adjunct.amount.units) == "milliliter"
    assert adjunct.amount.magnitude == pytest.approx(1.355)


@pytest.mark.django_db
def test_recipe_import_saves_the_fermentable_amount(client):
    _import_tart_cider(client)

    fermentable = Recipe.objects.get().fermentables.get()
    assert str(fermentable.amount.units) == "liter"
    assert fermentable.amount.magnitude == pytest.approx(19.8166808)


@pytest.mark.django_db
def test_recipe_import_reads_the_yeast_amount_is_weight_flag(client):
    _import_tart_cider(client, **{"<AMOUNT>0.0000000</AMOUNT>": "<AMOUNT>0.0236588</AMOUNT>"})

    yeast = Recipe.objects.get().yeasts.get()
    # Cider House Select is AMOUNT_IS_WEIGHT FALSE: a volume, not "FALSE" read as truthy.
    assert yeast.amount.dimensionality == Quantity(1, "liter").dimensionality


@pytest.mark.django_db
def test_recipe_import_creates_the_primary_use_when_the_database_has_none(client):
    from ..models import AdjunctUsage

    _import_tart_cider(client, with_primary=False)

    fermentable = Recipe.objects.get().fermentables.get()
    assert fermentable.intended_use == AdjunctUsage.objects.get(name__iexact="Primary")


@pytest.mark.django_db
def test_a_failed_recipe_import_saves_nothing_so_it_can_be_retried(client, monkeypatch):
    from ..models import Fermentable, RecipeYeasts

    def broken_save(self, *args, **kwargs):
        raise RuntimeError("disk full")

    # Fail on the last thing the import saves - after the recipe, fermentable and adjunct.
    monkeypatch.setattr(RecipeYeasts, "save", broken_save)
    response = _import_tart_cider(client)

    assert any("couldn't import" in str(m).lower() for m in response.context["messages"])
    assert not Recipe.objects.exists()
    assert not Fermentable.objects.exists()

    monkeypatch.undo()
    response = client.post(reverse("importData"),
                           {"recipe_upload": SimpleUploadedFile("tartcider.xml", TART_CIDER.read_bytes())})

    assert Recipe.objects.get().yeasts.count() == 1
    assert any("successfully imported" in str(m).lower() for m in response.context["messages"])
