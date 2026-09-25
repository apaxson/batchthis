"""
The old post_save signals (models.addActivity, models.addGravityTest):
- skip raw saves, so loaddata (e.g. moving the data to a new database) doesn't
  write extra ActivityLog rows or auto gravity tests;
- stamp rows with an aware timezone.now(), not the naive local datetime.now()
  that Django stored as UTC (hours off on a machine outside UTC).
"""
import warnings
from datetime import timedelta

import pytest
from django.utils import timezone

from ..factories import BatchFactory, FermenterFactory
from ..models import ActivityLog, BatchTest


@pytest.mark.django_db
def test_a_raw_batch_save_writes_no_activity_or_gravity_test():
    batch = BatchFactory.build(fermenter=FermenterFactory())
    logs_before = ActivityLog.objects.count()

    batch.save_base(raw=True)   # what loaddata does

    assert ActivityLog.objects.count() == logs_before
    assert not BatchTest.objects.filter(batch_id=batch.pk).exists()


@pytest.mark.django_db
def test_a_raw_batch_test_save_writes_no_activity():
    batch = BatchFactory()
    gravity = batch.tests.get()
    logs_before = ActivityLog.objects.count()

    gravity.save_base(raw=True)

    assert ActivityLog.objects.count() == logs_before


@pytest.mark.django_db
def test_a_normal_batch_save_still_logs_and_adds_the_starting_gravity():
    batch = BatchFactory()

    texts = list(batch.activity.values_list("text", flat=True))
    assert "Batch Created" in texts
    assert batch.tests.filter(description="Auto created from new batch.").count() == 1


@pytest.mark.django_db
def test_signal_rows_are_stamped_with_the_current_aware_time():
    with warnings.catch_warnings():
        warnings.filterwarnings("error", message=".*received a naive datetime.*")
        before = timezone.now()
        batch = BatchFactory()
        after = timezone.now()

    for stamp in list(batch.activity.values_list("datetime", flat=True)) + [batch.tests.get().datetime]:
        assert timezone.is_aware(stamp)
        assert before - timedelta(seconds=1) <= stamp <= after + timedelta(seconds=1)
