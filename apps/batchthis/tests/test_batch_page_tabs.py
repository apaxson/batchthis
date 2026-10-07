"""
Batch page tabs (UI item 3, Aaron 2026-10-07): Overview (readouts with their last
recorded date, what's next, the progress bar), Readings (charts and every test),
Details (batch recipe, plan, current vessel), Activity (notes and the activity log).
Each tab is part of the page address (#readings ...); Overview is the default.
"""
import datetime
import html as html_lib
import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from pint import Quantity

from ..factories import (
    AdjunctFactory,
    AdjunctUsageFactory,
    BatchFactory,
    FermentableFactory,
    FermenterFactory,
    RecipeFactory,
    VesselFactory,
)
from ..models import (
    BatchNote,
    BatchNoteType,
    BatchStage,
    BatchTest,
    BatchTestType,
    RecipeAdjunct,
    RecipeFermentable,
    Vessel,
)
from ..services import (
    add_activity_log,
    copy_recipe_ingredients_to_batch,
    transition_stage_event,
)

TABS = ("overview", "readings", "details", "activity")


@pytest.fixture
def client(db):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username="cellarhand", password="pw"))
    return client


@pytest.fixture
def batch(db):
    recipe = RecipeFactory(batchSize=Quantity(6, "gallons"), with_plan=True)
    use = AdjunctUsageFactory(name="Primary")
    honey = RecipeFermentable.objects.create(fermentable=FermentableFactory(name="Clover Honey"), intended_use=use,
                                             amount=Quantity(15, "lb"))
    nutrient = RecipeAdjunct.objects.create(adjunct=AdjunctFactory(name="Fermaid O"), intended_use=use,
                                            amount=Quantity(5, "gram"), time_to_add=Quantity(24, "hour"))
    honey.recipe.add(recipe)
    nutrient.recipe.add(recipe)
    vessel = VesselFactory(name="Carboy 7", status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(name="Tabbed batch", recipe=recipe, size=Quantity("6 gallons"),
                         fermenter=FermenterFactory(vessel=vessel), vessel=vessel)
    batch.startdate = timezone.now() - datetime.timedelta(days=20)
    batch.save()
    from ..services import copy_plan_to_batch
    copy_plan_to_batch(batch)
    copy_recipe_ingredients_to_batch(batch)
    transition_stage_event(batch, BatchStage.objects.get(shortid="pitch"), timestamp=timezone.now() - datetime.timedelta(days=10))
    return batch


def _page(client, batch):
    return client.get(reverse("batch", kwargs={"pk": batch.pk})).content.decode()


def _panel(page, name):
    """The HTML of one tab panel, and whether it's rendered hidden."""
    match = re.search(r'<section[^>]*id="tab-' + name + r'"[^>]*>(.*?)</section><!-- /tab-' + name + ' -->',
                      page, re.DOTALL)
    assert match, f"no panel tab-{name}"
    opening = re.search(r'<section[^>]*id="tab-' + name + r'"[^>]*>', page).group(0)
    return match.group(1), " hidden" in opening


def _text(fragment):
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


@pytest.mark.django_db
def test_four_tabs_link_to_their_page_address(client, batch):
    page = _page(client, batch)

    tablist = re.search(r'<nav[^>]*role="tablist"[^>]*data-cl-tabs[^>]*>(.*?)</nav>', page, re.DOTALL).group(1)
    assert re.findall(r'href="#(\w+)"', tablist) == list(TABS)
    assert 'aria-selected="true"' in re.search(r'<a[^>]*href="#overview"[^>]*>', tablist).group(0)


@pytest.mark.django_db
def test_overview_is_shown_by_default_and_the_rest_start_hidden(client, batch):
    page = _page(client, batch)

    assert [_panel(page, name)[1] for name in TABS] == [False, True, True, True]


@pytest.mark.django_db
def test_overview_has_readouts_with_last_recorded_dates_next_and_the_progress_bar(client, batch):
    nutrient = batch.ingredients.get(adjunct__name="Fermaid O")
    nutrient.time_to_add = Quantity(30, "day")          # still ahead, so there's something next
    nutrient.save()
    ph = BatchTest.objects.create(batch=batch, datetime=timezone.now() - datetime.timedelta(days=3), value="3.40",
                                  type=BatchTestType.objects.get(shortid="ph"))

    overview, _hidden = _panel(_page(client, batch), "overview")

    assert 'class="cl-readouts"' in overview and 'class="cl-next"' in overview and 'class="cl-progress"' in overview
    dates = re.findall(r'<div class="cl-readout-date[^"]*">(.*?)</div>', overview, re.DOTALL)
    assert len(dates) == 4
    assert timezone.localtime(ph.datetime).strftime("%b %-d") in _text(dates[1])            # pH
    assert _text(dates[3]).startswith("from SG on")                                         # Est. ABV


@pytest.mark.django_db
def test_a_readout_with_no_reading_says_so(client, batch):
    overview, _hidden = _panel(_page(client, batch), "overview")

    dates = re.findall(r'<div class="cl-readout-date[^"]*">(.*?)</div>', overview, re.DOTALL)
    assert _text(dates[2]) == "No reading yet"                                              # Free SO2


@pytest.mark.django_db
def test_readings_has_the_charts_and_every_test(client, batch):
    BatchTest.objects.create(batch=batch, datetime=timezone.now() - datetime.timedelta(days=2), value="6 ppm",
                             type=BatchTestType.objects.get(shortid="so2"), description="Add K-meta")

    readings, _hidden = _panel(_page(client, batch), "readings")

    assert 'id="chart-sg"' in readings and 'id="chart-so2"' in readings
    text = _text(readings)
    assert "Tests" in text and "6 ppm" in text and "Add K-meta" in text


@pytest.mark.django_db
def test_details_has_the_batch_recipe_plan_and_vessel(client, batch):
    details, _hidden = _panel(_page(client, batch), "details")

    text = _text(details)
    assert "Batch recipe" in text and "Clover Honey" in text and "15 lb" in text and "Pitch + 24 h" in text
    assert reverse("batchRecipe", kwargs={"pk": batch.pk}) in details
    assert "Plan" in text and "Current Vessel" in text and "Carboy 7" in text


@pytest.mark.django_db
def test_activity_has_every_note_and_the_activity_log(client, batch):
    for kind, text in (("Tasting Note", "Apricot nose"), ("General Note", "Clearing"), ("Fermentation Note", "Foaming")):
        BatchNote.objects.create(batch=batch, date=timezone.now(), text=text, notetype=BatchNoteType.objects.get(name=kind))
    add_activity_log(batch, "Racked off lees")

    activity, _hidden = _panel(_page(client, batch), "activity")

    text = _text(activity)
    for note in ("Apricot nose", "Clearing", "Foaming"):
        assert note in text
    assert "Activity log" in text and "Racked off lees" in text


@pytest.mark.django_db
def test_tab_counts(client, batch):
    BatchNote.objects.create(batch=batch, date=timezone.now(), text="One", notetype=BatchNoteType.objects.get(name="General Note"))

    page = _page(client, batch)
    tests = batch.tests.count()
    entries = batch.notes.count() + batch.activity.count()

    tablist = re.search(r'<nav[^>]*role="tablist"[^>]*>(.*?)</nav>', page, re.DOTALL).group(1)
    assert f'Readings<span class="cl-tab-count">{tests}</span>' in tablist
    assert f'Activity<span class="cl-tab-count">{entries}</span>' in tablist


@pytest.mark.django_db
def test_without_javascript_every_panel_shows(client, batch):
    page = _page(client, batch)

    noscript = re.search(r"<noscript>(.*?)</noscript>", page, re.DOTALL).group(1)
    assert "[role=tabpanel][hidden]" in noscript.replace('"', "") and "display: block" in noscript


@pytest.mark.django_db
def test_no_template_comment_leaks_onto_the_page(client, batch):
    page = _page(client, batch)

    assert "{#" not in page and "#}" not in page


# ---------- Readings: four equal chart cards, two per row (Aaron, 2026-10-07) ----------

@pytest.mark.django_db
def test_readings_has_four_chart_cards_including_temperature(client, batch):
    BatchTest.objects.create(batch=batch, datetime=timezone.now() - datetime.timedelta(days=1), value="64 °F",
                             type=BatchTestType.objects.get(shortid="temperature"))

    readings, _hidden = _panel(_page(client, batch), "readings")

    assert 'cl-instrument-grid--pairs' in readings
    titles = re.findall(r'<span class="cl-instrument-title">([^<]+)</span>', readings)
    assert titles == ["Specific gravity", "pH", "Free SO₂", "Temperature"]
    assert 'id="chart-temp"' in readings and "64 °F" in _text(readings)


@pytest.mark.django_db
def test_a_chart_with_no_readings_keeps_its_card_as_an_empty_frame(client, batch):
    readings, _hidden = _panel(_page(client, batch), "readings")

    assert readings.count('class="cl-instrument"') == 4
    assert readings.count('class="cl-chart-empty"') == 3          # pH, SO2, temperature have no readings
    assert "No temperature readings yet." in _text(readings)


@pytest.mark.django_db
def test_low_so2_shows_its_current_value_in_red(client, batch):
    BatchTest.objects.create(batch=batch, datetime=timezone.now(), value="6 ppm", type=BatchTestType.objects.get(shortid="so2"))

    readings, _hidden = _panel(_page(client, batch), "readings")

    assert re.search(r'cl-instrument-current cl-instrument-current--fault">6 ppm<', readings)


@pytest.mark.django_db
def test_the_tests_table_is_outside_the_chart_grid(client, batch):
    readings, _hidden = _panel(_page(client, batch), "readings")

    grid_end = readings.index('class="cl-section-title">Tests')
    # Everything before the Tests title is closed except the Tests panel, its head and its title div.
    assert readings[:grid_end].count("<div") == readings[:grid_end].count("</div>") + 3
