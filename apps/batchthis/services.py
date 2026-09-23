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
                previous_status=previous,
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


# Out of Service is manual and outside the normal Ready -> In Use -> Needs
# Cleaning cycle. An In Use vessel must be emptied first (Batch.transfer() or
# Batch.complete() leaves it Needs Cleaning).
OUT_OF_SERVICE_FROM = (Vessel.STATUS_READY, Vessel.STATUS_DIRTY)


def take_vessel_out_of_service(vessel: Vessel, reason: str) -> VesselStatusEvent:
    """Clean/Ready or Needs Cleaning -> Out of Service. The reason is required and becomes the event's notes."""
    reason = (reason or "").strip()
    logger.debug(f"take_vessel_out_of_service: vessel={vessel.pk} '{vessel.name}' status={vessel.status!r} reason={reason!r}")

    problem = None
    if not reason:
        problem = "A reason is required to take a vessel out of service."
    elif vessel.status == Vessel.STATUS_OUT:
        problem = f"'{vessel.name}' is already {Vessel.STATUS_OUT}."
    elif vessel.status not in OUT_OF_SERVICE_FROM:
        problem = (f"'{vessel.name}' is {vessel.status}. Transfer or complete its batch first - "
                   f"only a {Vessel.STATUS_READY} or {Vessel.STATUS_DIRTY} vessel can go {Vessel.STATUS_OUT}.")
    elif vessel.current_batch is not None:
        problem = f"'{vessel.name}' still holds batch '{vessel.current_batch.name}'. Transfer or complete it first."
    if problem:
        logger.error(f"take_vessel_out_of_service: rejected for vessel {vessel.pk} - {problem}")
        raise ValidationError(problem)

    event = set_vessel_status(vessel, Vessel.STATUS_OUT, notes=reason[:250])
    logger.info(f"Vessel '{vessel.name}' taken out of service: {reason!r}")
    return event


def status_before_out_of_service(vessel: Vessel) -> str:
    """
    The status an Out of Service vessel goes back to: whatever it was in when
    it was taken out. Falls back to Needs Cleaning (the safe choice after a
    repair) when no event records it, e.g. status hand-set in Django admin.
    """
    event = (
        vessel.status_events.filter(status=Vessel.STATUS_OUT)
        .order_by('-timestamp', '-pk')
        .first()
    )
    if event and event.previous_status in OUT_OF_SERVICE_FROM:
        return event.previous_status
    logger.debug(f"status_before_out_of_service: no prior status recorded for vessel {vessel.pk}; using {Vessel.STATUS_DIRTY!r}")
    return Vessel.STATUS_DIRTY


def return_vessel_to_service(vessel: Vessel, notes: str = "") -> VesselStatusEvent:
    """Out of Service -> the status it had before it was taken out."""
    logger.debug(f"return_vessel_to_service: vessel={vessel.pk} '{vessel.name}' status={vessel.status!r}")
    if vessel.status != Vessel.STATUS_OUT:
        problem = f"'{vessel.name}' is {vessel.status}, not {Vessel.STATUS_OUT}."
        logger.error(f"return_vessel_to_service: rejected for vessel {vessel.pk} - {problem}")
        raise ValidationError(problem)

    restored = status_before_out_of_service(vessel)
    event = set_vessel_status(vessel, restored, notes=(notes or "").strip()[:250] or "Returned to service")
    logger.info(f"Vessel '{vessel.name}' returned to service as {restored!r}")
    return event
