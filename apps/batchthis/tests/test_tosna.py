"""
TOSNA 2.0 calculator (TODO.txt "TOSNA 2.0 CALCULATOR"; Aaron 2026-10-05):

    total Fermaid-O (g) = ((Brix x 10) x N factor / 50) x batch gallons
    N factor: low 0.75, medium 0.90, high 1.25
    four equal additions: 24 h, 48 h, 72 h after Pitch, and day 7 or the 1/3
    sugar break, whichever comes first.

Source: https://www.meadmaderight.com/nutrient-additions
"""
import html

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from pint import Quantity

from ..factories import (
    AdjunctFactory,
    AdjunctUsageFactory,
    FermentableFactory,
    RecipeFactory,
    YeastFactory,
)
from ..models import (
    Adjunct,
    AdjunctUsage,
    RecipeAdjunct,
    RecipeFermentable,
    RecipeYeasts,
    Yeast,
)
from ..services import apply_tosna_to_recipe, tosna_schedule

GALLON_LITERS = 3.785411784


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


@pytest.fixture
def primary(db):
    return AdjunctUsage.objects.filter(name="Primary").first() or AdjunctUsageFactory(name="Primary")


@pytest.fixture
def fermaid(db):
    return Adjunct.objects.filter(name="Fermaid O").first() or AdjunctFactory(name="Fermaid O")


@pytest.fixture
def recipe(primary, fermaid):
    """5 gallons, OG 1.100 / FG 1.000, one yeast with no nitrogen demand yet, honey, and an old Fermaid O row."""
    recipe = RecipeFactory(name="Traditional", batchSize=Quantity(5, "gallons"),
                           estOG=Quantity(1.100, "sg"), estFG=Quantity(1.000, "sg"))
    honey = RecipeFermentable.objects.create(fermentable=FermentableFactory(name="Orange Blossom Honey"),
                                             intended_use=primary, amount=Quantity(15, "lb"))
    old = RecipeAdjunct.objects.create(adjunct=fermaid, intended_use=primary, amount=Quantity(5, "gram"),
                                       time_to_add=Quantity(1, "day"), recipe_notes="Old schedule")
    tannin = RecipeAdjunct.objects.create(adjunct=AdjunctFactory(name="FT Rouge"), intended_use=primary,
                                          amount=Quantity(4, "gram"), time_to_add=Quantity(0, "hour"))
    yeast = RecipeYeasts.objects.create(yeast=YeastFactory(name="Lalvin 71B"), amount=Quantity(5, "gram"))
    for item in (honey, old, tannin, yeast):
        item.recipe.add(recipe)
    return recipe


def _fermaid_rows(recipe):
    return list(recipe.adjuncts.filter(adjunct__name="Fermaid O").order_by("pk"))   # created in dose order


# ---------- The formula ----------

def test_the_tosna_2_example_1_100_five_gallons_medium():
    schedule = tosna_schedule(1.100, Quantity(5, "gallons"), "medium")

    assert schedule.brix == pytest.approx(23.77, abs=0.01)
    assert schedule.yan_ppm == pytest.approx(213.97, abs=0.05)                 # Brix x 10 x 0.90
    assert schedule.total_g == pytest.approx(21.397, abs=0.01)                # / 50 x 5 gal
    assert [a.grams for a in schedule.additions] == [pytest.approx(5.349, abs=0.005)] * 4


@pytest.mark.parametrize("nitrogen, factor", [("low", 0.75), ("medium", 0.90), ("high", 1.25)])
def test_the_nitrogen_demand_factor(nitrogen, factor):
    schedule = tosna_schedule(1.100, Quantity(1, "gallon"), nitrogen)

    assert schedule.total_g == pytest.approx(schedule.brix * 10 * factor / 50)


def test_a_batch_size_in_liters_is_converted_to_gallons():
    liters = tosna_schedule(1.100, Quantity(5 * GALLON_LITERS, "liters"), "medium")

    assert liters.total_g == pytest.approx(tosna_schedule(1.100, Quantity(5, "gallons"), "medium").total_g)


def test_the_four_additions_are_timed_from_pitch_with_notes():
    additions = tosna_schedule(1.100, Quantity(5, "gallons"), "medium", end_sg=1.000).additions

    assert [a.offset.to("hour").magnitude for a in additions] == [24, 48, 72, 168]
    assert additions[0].note == "TOSNA 2.0 - 1 of 4 (24 h after pitch)"
    assert additions[3].note == ("TOSNA 2.0 - 4 of 4: day 7 or the 1/3 sugar break (SG 1.067), "
                                 "whichever comes first")


def test_without_an_end_gravity_the_last_note_has_no_break_gravity():
    schedule = tosna_schedule(1.100, Quantity(5, "gallons"), "medium")

    assert schedule.break_sg is None
    assert schedule.additions[3].note == "TOSNA 2.0 - 4 of 4: day 7 or the 1/3 sugar break, whichever comes first"


@pytest.mark.parametrize("sg, size, nitrogen", [
    (1.000, "5 gallons", "medium"), (1.250, "5 gallons", "medium"), (0.990, "5 gallons", "medium"),
    (1.100, "0 gallons", "medium"), (1.100, "5 lb", "medium"), (1.100, "5 gallons", ""),
    (1.100, "5 gallons", "extreme"),
])
def test_bad_inputs_are_rejected(sg, size, nitrogen):
    with pytest.raises(ValidationError):
        tosna_schedule(sg, Quantity(size), nitrogen)


# ---------- Adding it to a recipe ----------

@pytest.mark.django_db
def test_replace_swaps_the_recipes_fermaid_o_rows_for_the_four_doses(recipe, primary):
    schedule = tosna_schedule(1.100, Quantity(5, "gallons"), "medium", end_sg=1.000)

    apply_tosna_to_recipe(recipe, schedule, "medium", replace=True)

    rows = _fermaid_rows(recipe)
    assert [r.time_to_add.to("hour").magnitude for r in rows] == [24, 48, 72, 168]
    assert all(r.amount.to("gram").magnitude == pytest.approx(5.349, abs=0.005) for r in rows)
    assert all(r.intended_use == primary for r in rows)
    assert rows[0].recipe_notes == "TOSNA 2.0 - 1 of 4 (24 h after pitch)"
    assert "Old schedule" not in [r.recipe_notes for r in rows]
    assert recipe.adjuncts.filter(adjunct__name="FT Rouge").exists()               # other adjuncts kept


@pytest.mark.django_db
def test_add_alongside_keeps_the_old_fermaid_o_rows(recipe):
    apply_tosna_to_recipe(recipe, tosna_schedule(1.100, Quantity(5, "gallons"), "medium"), "medium", replace=False)

    assert len(_fermaid_rows(recipe)) == 5


@pytest.mark.django_db
def test_the_nitrogen_demand_is_saved_on_the_yeast(recipe):
    apply_tosna_to_recipe(recipe, tosna_schedule(1.100, Quantity(5, "gallons"), "high"), "high", replace=True)

    assert Yeast.objects.get(name="Lalvin 71B").nitrogen_requirement == "high"


@pytest.mark.django_db
def test_without_fermaid_o_nothing_is_saved(recipe, fermaid):
    fermaid.name = "Fermaid O (old)"
    fermaid.save()

    with pytest.raises(ValidationError, match="Fermaid O"):
        apply_tosna_to_recipe(recipe, tosna_schedule(1.100, Quantity(5, "gallons"), "medium"), "medium", replace=True)

    assert recipe.adjuncts.count() == 2 and Yeast.objects.get(name="Lalvin 71B").nitrogen_requirement == ""


@pytest.mark.django_db
def test_a_recipe_without_exactly_one_yeast_is_rejected(recipe):
    second = RecipeYeasts.objects.create(yeast=YeastFactory(name="EC-1118"), amount=Quantity(5, "gram"))
    second.recipe.add(recipe)

    with pytest.raises(ValidationError, match="one yeast"):
        apply_tosna_to_recipe(recipe, tosna_schedule(1.100, Quantity(5, "gallons"), "medium"), "medium", replace=True)


# ---------- API ----------

@pytest.mark.django_db
def test_live_calculation_api(client):
    response = client.get(reverse("tosna-calc"), {"sg": "1.100", "size": "5 gallons", "nitrogen": "medium",
                                                  "end_sg": "1.000"})

    data = response.json()
    assert response.status_code == 200
    assert data["total_g"] == pytest.approx(21.4, abs=0.01) and data["per_addition_g"] == pytest.approx(5.35, abs=0.01)
    assert data["break_sg"] == "1.067"
    assert [a["when"] for a in data["additions"]] == ["Pitch + 24 h", "Pitch + 48 h", "Pitch + 72 h",
                                                      "Day 7 or 1/3 sugar break"]


@pytest.mark.django_db
@pytest.mark.parametrize("params", [{"sg": "1.100", "size": "5 gallons"}, {"sg": "abc", "size": "5 gallons",
                                    "nitrogen": "low"}, {"sg": "1.100", "size": "5", "nitrogen": "low"}])
def test_live_calculation_rejects_bad_input(client, params):
    response = client.get(reverse("tosna-calc"), params)

    assert response.status_code == 400 and response.json()["error"]


@pytest.mark.django_db
def test_recipe_api_returns_inputs_and_current_rows(client, recipe):
    data = client.get(reverse("recipe-tosna", kwargs={"pk": recipe.pk})).json()

    assert data["sg"] == "1.100" and data["end_sg"] == "1.000" and data["size"] == "5 gal"
    assert data["yeast"] == {"name": "Lalvin 71B", "nitrogen": ""}
    assert data["current"] == [{"amount": "5 g", "when": "Pitch + 24 h", "notes": "Old schedule"}]


@pytest.mark.django_db
def test_applying_from_the_recipe_requires_nitrogen_demand(client, recipe):
    url = reverse("recipe-tosna", kwargs={"pk": recipe.pk})

    missing = client.post(url, {"nitrogen": "", "replace": "true"})
    saved = client.post(url, {"nitrogen": "medium", "replace": "true"})

    assert missing.status_code == 400 and "nitrogen" in missing.json()["error"].lower()
    assert saved.status_code == 200 and saved.json() == {"saved": True}
    assert len(_fermaid_rows(recipe)) == 4


@pytest.mark.django_db
def test_tosna_api_requires_login(recipe):
    assert Client().get(reverse("tosna-calc"), {"sg": "1.1"}).status_code in (401, 403)
    assert Client().post(reverse("recipe-tosna", kwargs={"pk": recipe.pk}), {"nitrogen": "low"}).status_code in (401, 403)


# ---------- Pages ----------

@pytest.mark.django_db
def test_recipe_page_offers_tosna_and_shows_nitrogen_demand(client, recipe):
    page = html.unescape(client.get(reverse("recipe", kwargs={"pk": recipe.pk})).content.decode())

    assert "data-cl-tosna" in page and reverse("recipe-tosna", kwargs={"pk": recipe.pk}) in page
    assert "N demand" in page


@pytest.mark.django_db
def test_tools_page_and_sidebar_have_the_calculator(client):
    page = client.get(reverse("tosnaCalculator")).content.decode()

    assert "TOSNA" in page and 'data-tosna-calculator' in page
    assert reverse("tosnaCalculator") in client.get(reverse("index")).content.decode()


@pytest.mark.django_db
def test_every_page_has_the_shared_tosna_modal(client):
    assert 'id="tosnaModal"' in client.get(reverse("index")).content.decode()
