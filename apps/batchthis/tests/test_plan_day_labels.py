"""Plan displays show whole days (Aaron, 2026-10-02) - no decimals in the plan tables."""
import pytest

from ..views.main import _days_label, _difference_label


@pytest.mark.parametrize("days, label", [
    (17.5, "18 days"), (16.4, "16 days"), (1.2, "1 day"), (0.4, "under 1 day"), (0, "0 days"), (49, "49 days"),
])
def test_days_label_is_whole_days(days, label):
    assert _days_label(days) == label


@pytest.mark.parametrize("days, label", [(3.4, "+3 days"), (-2.6, "−3 days"), (0.3, "On plan"), (-0.4, "On plan")])
def test_difference_label_is_whole_days(days, label):
    assert _difference_label(days) == label
