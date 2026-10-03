"""
BROWSER TIME ZONE (TODO.txt; Aaron 2026-10-03): show dates/times in the viewer's
browser time zone. includes/_browser_timezone.html stores the browser's IANA zone
("America/Chicago") in the "tz" cookie; this middleware activates it for the
request, so templates (|date, timesince, {% now %}), timezone.localtime() /
localdate(), datetime form inputs and DRF output all follow it. Storage stays UTC.
Without a valid zone - no cookie yet, no JavaScript, a bad value - times are UTC
(TIME_ZONE).
"""
import logging
from functools import lru_cache
from urllib.parse import unquote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

logger = logging.getLogger(__name__)

TIMEZONE_COOKIE = 'tz'


@lru_cache(maxsize=64)
def browser_zone(name: str) -> ZoneInfo | None:
    """The ZoneInfo for a cookie value, or None if it isn't a real IANA zone name."""
    name = unquote(name or "").strip()
    if not name or len(name) > 64:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as e:
        logger.debug("browser_zone: ignoring time zone cookie %r (%s)", name, e)
        return None


class BrowserTimezoneMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        zone = browser_zone(request.COOKIES.get(TIMEZONE_COOKIE, ""))
        if zone is not None:
            timezone.activate(zone)
        else:
            timezone.deactivate()    # TIME_ZONE (UTC)
        request.browser_timezone = zone
        try:
            return self.get_response(request)
        finally:
            # The zone is per thread - never let one viewer's zone carry into the next request.
            timezone.deactivate()
