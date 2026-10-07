"""
How measurements read on screen and in edit boxes (UI item 1, Aaron 2026-10-07): one
place for "6 gal", "20 L", "3.072 g", "14 days", "Pitch + 24 h" - used by services,
template filters (templatetags/batch_extras.py), the API serializers, form fields
(fields.MeasurementField.prepare_value) and Vessel.__str__. Stored values never change.
"""
from django.conf import settings


def as_quantity(value):
    """`value` as a Quantity of the project's unit registry (accepts text or another registry's Quantity)."""
    ureg = settings.DJANGO_PINT_UNIT_REGISTER
    if value is None or isinstance(value, ureg.Quantity):
        return value
    if isinstance(value, str):
        return ureg.Quantity(value)
    return ureg.Quantity(value.magnitude, str(value.units))


def days_label(days: float) -> str:
    """Whole days (Aaron, 2026-10-02: no decimals in plan displays); a part day reads "under 1 day"."""
    whole = round(days)
    if whole == 0 and days:
        return "under 1 day"
    return f"{whole} day" if whole == 1 else f"{whole} days"


def quantity_label(value, digits: int = 4) -> str:
    """A short amount for display: "24 lb", "10 g", "2.5 kg", "6 gal" ("" for none); `digits` significant figures."""
    if isinstance(value, (int, float)):
        return f"{value:.{digits}g}"      # a bare number, e.g. the dashboard's total with no batches (0)
    value = as_quantity(value)
    if value is None:
        return ""
    # Imported recipes store small amounts in kg/L ("0.005814 kg"); show those as g/ml.
    smaller = {'kilogram': 'gram', 'liter': 'milliliter'}.get(str(value.units))
    if smaller and abs(value.magnitude) < 1:
        value = value.to(smaller)
    unit = "L" if str(value.units) == 'liter' else f"{value.units:~}"   # "10 L", not "10 l" (reads as 1)
    return f"{value.magnitude:.{digits}g} {unit}"


def quantity_input(value) -> str:
    """
    A stored measurement as edit-box text that reads like the display and re-saves to
    the same amount: "6 gal", "20 L", "3.07179 g"; durations in words, "30 days",
    "24 hours", "2 weeks" ("" for none).
    """
    value = as_quantity(value)
    if value is None:
        return ""
    if value.check('[time]'):
        magnitude, unit = value.magnitude, str(value.units)
        return f"{magnitude:.6g} {unit}" + ("" if magnitude == 1 else "s")
    return quantity_label(value, digits=6)


def time_to_add_label(value) -> str:
    """When an addition goes in, after Pitch: "At pitch", "Pitch + 24 h", "Pitch + 7 d"."""
    value = as_quantity(value)
    if value is None:
        return ""
    hours = value.to('hour').magnitude
    if hours <= 0:
        return "At pitch"
    if hours < 1:
        return f"Pitch + {value.to('minute').magnitude:.0f} min"
    if hours <= 72:
        return f"Pitch + {hours:.3g} h"
    return f"Pitch + {value.to('day').magnitude:.3g} d"
