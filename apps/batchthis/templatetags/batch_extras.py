"""Display filters for measurements, sharing the labels the JSON APIs use (services.py)."""
from django import template

from ..services import quantity_label, time_to_add_label

register = template.Library()


@register.filter
def amount(value) -> str:
    """{{ ingredient.amount|amount }} -> "24 lb", "3.072 g" ("" for none)."""
    return quantity_label(value)


@register.filter
def time_to_add(value) -> str:
    """{{ ingredient.time_to_add|time_to_add }} -> "At pitch", "Pitch + 24 h", "Pitch + 7 d"."""
    return time_to_add_label(value)
