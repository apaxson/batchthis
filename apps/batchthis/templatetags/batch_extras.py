"""Display filters for measurements, sharing the labels the JSON APIs use (services.py)."""
from django import template

from ..services import days_label, quantity_label, time_to_add_label

register = template.Library()


@register.filter
def amount(value) -> str:
    """{{ ingredient.amount|amount }} -> "24 lb", "3.072 g" ("" for none)."""
    return quantity_label(value)


@register.filter
def time_to_add(value) -> str:
    """{{ ingredient.time_to_add|time_to_add }} -> "At pitch", "Pitch + 24 h", "Pitch + 7 d"."""
    return time_to_add_label(value)


@register.filter
def days(value) -> str:
    """{{ step.planned_days|days }} -> "14 days", "1 day", "under 1 day" ("" for none) - whole days in plan tables."""
    return "" if value is None else days_label(value)
