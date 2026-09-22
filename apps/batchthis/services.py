import logging
from typing import Optional

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Batch, Vessel, VesselStatusEvent

logger = logging.getLogger(__name__)

VALID_VESSEL_STATUSES = {status for status, _ in Vessel.STATUS_CHOICES}


def set_vessel_status(
    vessel: Vessel,
    status: str,
    batch: Optional[Batch] = None,
    notes: str = "",
) -> VesselStatusEvent:
    """
    The single place a Vessel's status is allowed to change (see
    TODO-VesselLifecycle.txt). Updates vessel.status and appends the matching
    VesselStatusEvent in one transaction, so the cached status and the history
    log can't drift apart.

    Any valid status is accepted, including a repeat of the current one - no
    state-machine enforcement in v1. Vessel history is kept only in
    VesselStatusEvent; ActivityLog is reserved for Batch actions.
    """
    logger.debug(
        f"set_vessel_status: vessel={vessel.pk} '{vessel.name}' "
        f"{vessel.status!r} -> {status!r}, batch={getattr(batch, 'pk', None)}, notes={notes!r}"
    )

    if status not in VALID_VESSEL_STATUSES:
        logger.error(
            f"Rejected invalid status {status!r} for vessel {vessel.pk} '{vessel.name}'. "
            f"Valid statuses: {sorted(VALID_VESSEL_STATUSES)}"
        )
        raise ValidationError(f"Invalid vessel status: {status!r}")

    previous = vessel.status
    try:
        with transaction.atomic():
            vessel.status = status
            vessel.save(update_fields=["status"])
            event = VesselStatusEvent.objects.create(
                vessel=vessel,
                status=status,
                batch=batch,
                notes=notes,
                timestamp=timezone.now(),
            )
    except Exception:
        # The DB rolled back; keep the in-memory instance consistent with it.
        vessel.status = previous
        logger.exception(
            f"Failed to log status {status!r} for vessel {vessel.pk} '{vessel.name}'"
        )
        raise

    logger.info(f"Vessel '{vessel.name}' status {previous!r} -> {status!r}")
    return event
