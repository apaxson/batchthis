import datetime

import pytest

from ..factories import BatchFactory
from ..forms import BatchNoteForm, BatchStageForm, BatchTestForm, DateTimeWidget

WHEN = datetime.datetime(2026, 9, 23, 10, 30, tzinfo=datetime.UTC)


def test_datetime_widget_renders_the_format_datetime_local_inputs_accept():
    # <input type="datetime-local"> only accepts YYYY-MM-DDTHH:MM; a slash
    # format makes browsers show the field blank.
    html = DateTimeWidget().render("when", WHEN)

    assert 'type="datetime-local"' in html
    assert 'value="2026-09-23T10:30"' in html


@pytest.mark.parametrize("form_class, field", [(BatchTestForm, "datetime"), (BatchNoteForm, "date")])
def test_test_and_note_forms_prefill_their_date_time_field(form_class, field):
    form = form_class(initial={field: WHEN})

    assert 'value="2026-09-23T10:30"' in str(form[field])


@pytest.mark.parametrize("form_class, field", [(BatchTestForm, "datetime"), (BatchNoteForm, "date")])
def test_test_and_note_forms_accept_what_the_browser_submits(form_class, field):
    form = form_class(data={field: "2026-09-23T10:30"})
    form.is_valid()

    assert field not in form.errors


@pytest.mark.django_db
def test_stage_form_uses_the_same_widget():
    form = BatchStageForm(batch=BatchFactory(), initial={"timestamp": WHEN})

    assert isinstance(form.fields["timestamp"].widget, DateTimeWidget)
    assert 'value="2026-09-23T10:30"' in str(form["timestamp"])
