"""
BROWSER TIME ZONE (TODO.txt; Aaron 2026-10-03): dates/times show in the viewer's
browser time zone - reported in a "tz" cookie, activated per request by
BrowserTimezoneMiddleware - and fall back to UTC when it can't be determined.
Storage stays UTC.
"""
import datetime
import re
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from ..factories import BatchFactory, FermenterFactory, RecipeFactory, VesselFactory
from ..models import (
    BatchNote,
    BatchNoteType,
    BatchStageEvent,
    BatchTest,
    BatchTestType,
    Vessel,
)
from ..views.main import _build_series

UTC = datetime.UTC
CHICAGO = "America/Chicago"
# 03:00 UTC on Oct 3 is 22:00 on Oct 2 in Chicago (CDT, UTC-5).
LATE_EVENING = datetime.datetime(2026, 10, 3, 3, 0, tzinfo=UTC)


def _client(tz=None):
    client = Client()
    client.force_login(get_user_model().objects.create_user(username=f"cellarhand-{tz}", password="pw"))
    if tz is not None:
        client.cookies["tz"] = tz
    return client


def _batch():
    vessel = VesselFactory(status=Vessel.STATUS_ACTIVE)
    batch = BatchFactory(fermenter=FermenterFactory(vessel=vessel), vessel=vessel)
    batch.startdate = LATE_EVENING
    batch.save()
    return batch


def _started(client, batch):
    page = client.get(reverse("batch", kwargs={"pk": batch.pk})).content.decode()
    return re.search(r'Started <b class="cl-num">([^<]+)</b>', page).group(1)


# ---------- Pages follow the browser's zone ----------

@pytest.mark.django_db
def test_pages_show_times_in_the_browsers_time_zone():
    batch = _batch()

    assert _started(_client(CHICAGO), batch) == "Oct 2, 2026"


@pytest.mark.django_db
@pytest.mark.parametrize("cookie", [None, "", "Mars/Olympus_Mons", "../../etc/passwd", "x" * 200])
def test_without_a_valid_browser_zone_times_are_utc(cookie):
    batch = _batch()

    assert _started(_client(cookie), batch) == "Oct 3, 2026"


@pytest.mark.django_db
def test_one_viewers_zone_doesnt_leak_into_the_next_request():
    batch = _batch()
    _started(_client(CHICAGO), batch)

    assert _started(_client(), batch) == "Oct 3, 2026"
    assert timezone.get_current_timezone_name() == "UTC"


# ---------- Typed dates/times are read in the browser's zone ----------

@pytest.mark.django_db
def test_a_typed_date_time_is_stored_as_the_right_utc_instant():
    batch = _batch()
    data = {"date": "2026-10-02T21:00", "notetype": BatchNoteType.objects.get(name="General Note").pk,
            "text": "Racked tonight"}

    _client(CHICAGO).post(reverse("addDetailNote", kwargs={"pk": batch.pk}), data)

    note = BatchNote.objects.get(text="Racked tonight")
    assert note.date == datetime.datetime(2026, 10, 3, 2, 0, tzinfo=UTC)


@pytest.mark.django_db
def test_add_batch_reads_an_earlier_start_date_in_the_browsers_zone():
    fermenter = FermenterFactory(vessel=VesselFactory(status=Vessel.STATUS_READY))
    data = {"name": "Chicago batch", "startdate": "2026-09-01", "size": "6 gallons", "fermenter": fermenter.pk,
            "startingGravity": "1.100", "estimatedEndGravity": "1.010",
            "recipe": RecipeFactory(with_plan=True).pk, "workflow_template": ""}

    _client(CHICAGO).post(reverse("addBatch"), data)

    from ..models import Batch
    start = Batch.objects.get(name="Chicago batch").startdate
    assert start == datetime.datetime(2026, 9, 1, 5, 0, tzinfo=UTC)          # midnight in Chicago


@pytest.mark.django_db
def test_forms_default_to_now_in_the_browsers_zone():
    batch = _batch()

    page = _client(CHICAGO).get(reverse("addDetailNote", kwargs={"pk": batch.pk})).content.decode()

    value = re.search(r'name="date" value="([^"]+)"', page).group(1)
    shown = datetime.datetime.fromisoformat(value).replace(tzinfo=ZoneInfo(CHICAGO))
    assert abs(shown - timezone.now()) < datetime.timedelta(minutes=2)


# ---------- Places that formatted UTC directly ----------

@pytest.mark.django_db
def test_chart_labels_use_the_current_zone():
    batch = _batch()
    BatchTest.objects.create(batch=batch, datetime=LATE_EVENING, value="1.050 sg",
                             type=BatchTestType.objects.get(shortid="specific-gravity"))

    with timezone.override(ZoneInfo(CHICAGO)):
        series = _build_series(batch, "specific-gravity")

    assert "10/02/26" in series["dates"]


@pytest.mark.django_db
def test_event_text_uses_the_current_zone():
    batch = _batch()
    note = BatchNote.objects.create(batch=batch, date=LATE_EVENING, text="Late check",
                                    notetype=BatchNoteType.objects.get(name="General Note"))
    event = BatchStageEvent(batch=batch, timestamp=LATE_EVENING)        # a transfer-only event, unsaved

    with timezone.override(ZoneInfo(CHICAGO)):
        assert str(note).startswith("10/02/26-22:00")
        assert "10/02/26-22:00" in str(event)


@pytest.mark.django_db
def test_batch_api_dates_follow_the_browsers_zone():
    batch = _batch()

    rows = _client(CHICAGO).get(reverse("batch-list")).json()

    assert next(r for r in rows if r["id"] == batch.pk)["startdate"] == "2026-10-02"


# ---------- The browser reports its zone ----------

@pytest.mark.django_db
def test_every_app_page_and_the_login_page_report_the_browser_zone():
    app_page = _client().get(reverse("index")).content.decode()
    login_page = Client().get(reverse("login")).content.decode()

    for page in (app_page, login_page):
        assert "Intl.DateTimeFormat().resolvedOptions().timeZone" in page
        assert "tz=" in page


@pytest.mark.django_db
def test_the_report_script_never_reloads_a_post_response():
    batch = _batch()
    data = {"date": "", "notetype": BatchNoteType.objects.get(name="General Note").pk, "text": ""}

    page = _client().post(reverse("addDetailNote", kwargs={"pk": batch.pk}), data).content.decode()

    assert "data-tz-reload" not in page or 'data-tz-reload="0"' in page
