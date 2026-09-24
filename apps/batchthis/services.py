import logging
from datetime import date, datetime
from typing import Optional

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    ActivityLog,
    AgingTank,
    Barrel,
    Batch,
    BatchStage,
    BatchStageEvent,
    Fermenter,
    Vessel,
    VesselStatusEvent,
)

logger = logging.getLogger(__name__)

VALID_VESSEL_STATUSES = {status for status, _ in Vessel.STATUS_CHOICES}


def set_vessel_status(
    vessel: Vessel,
    status: str,
    batch: Optional[Batch] = None,
    notes: str = "",
    timestamp: Optional[datetime] = None,
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
                timestamp=timestamp or timezone.now(),
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


# ---------- Batch stage workflow (TODO-BatchStage.txt) ----------

def add_batch_log(
    batch: Batch,
    stage: Optional[BatchStage],
    *,
    timestamp: datetime,
    vessel: Optional[Vessel],
    notes: str = "",
) -> BatchStageEvent:
    """Write one row of the batch's stage log. No workflow checks - see transition_stage_event()."""
    event = BatchStageEvent.objects.create(
        batch=batch, stage=stage, timestamp=timestamp, vessel=vessel, notes=notes
    )
    logger.debug(f"add_batch_log: batch={batch.pk} stage={getattr(stage, 'shortid', None)!r} vessel={getattr(vessel, 'pk', None)} at {timestamp}")
    return event


def add_activity_log(batch: Batch, text: str, *, timestamp: Optional[datetime] = None) -> ActivityLog:
    """Add one entry to the batch's ActivityLog."""
    entry = ActivityLog.objects.create(datetime=timestamp or timezone.now(), text=text)
    batch.activity.add(entry)
    logger.debug(f"add_activity_log: batch={batch.pk} {text!r}")
    return entry


def _workflow_problem(batch: Batch, stage: BatchStage, current: Optional[BatchStageEvent]) -> Optional[str]:
    """Why the workflow doesn't allow this stage next (ignoring timestamp/vessel), or None."""
    state = current.stage.to_state if current else None
    if state == BatchStage.STATE_COMPLETED or not batch.active:
        return f"Batch '{batch.name}' is complete; no more stages can be logged."
    if current is None and stage.from_state != "":
        return f"Log Pitch first - '{batch.name}' hasn't been pitched yet."
    if current is not None:
        repeat_racking = stage.shortid == BatchStage.RACKING and state == BatchStage.STATE_AGING
        if stage.from_state != state and not repeat_racking:
            expected = stage.get_from_state_display()
            return f"{stage.name} needs a batch in {expected}; '{batch.name}' is in {state}."
    return None


def allowed_next_stages(batch: Batch) -> list[BatchStage]:
    """The stages the workflow allows next for this batch, in workflow order."""
    current = batch.current_stage_event
    stages = [stage for stage in BatchStage.objects.all() if _workflow_problem(batch, stage, current) is None]
    logger.debug(f"allowed_next_stages: batch={batch.pk} -> {[s.shortid for s in stages]}")
    return stages


def _timestamp_problem(batch: Batch, timestamp: datetime, what: str) -> Optional[str]:
    """Stages and transfers can be backdated, but not into the future or before the batch's latest event."""
    if timestamp > timezone.now():
        return f"{what} can't be logged in the future."
    latest = batch.stage_events.select_related('stage').order_by('-timestamp', '-pk').first()
    if latest is not None and timestamp < latest.timestamp:
        return (f"{what} can't be earlier than the batch's latest stage or transfer "
                f"({latest.label}, {timezone.localtime(latest.timestamp):%b %d, %Y %H:%M}).")
    if timestamp < batch.startdate:
        return f"{what} can't be before the batch's start date ({timezone.localtime(batch.startdate):%b %d, %Y})."
    return None


def _stage_problem(batch: Batch, stage: BatchStage, timestamp: datetime, dst_vessel: Optional[Vessel]) -> Optional[str]:
    """Why this stage can't be logged on this batch right now, or None if it can."""
    current = batch.current_stage_event
    problem = _workflow_problem(batch, stage, current)
    if problem:
        return problem

    problem = _timestamp_problem(batch, timestamp, stage.name)
    if problem:
        return problem

    if stage.transfers_batch and dst_vessel is None:
        return f"{stage.name} transfers the batch - choose a destination vessel."
    if not stage.transfers_batch and dst_vessel is not None:
        return f"{stage.name} doesn't transfer the batch - don't choose a destination vessel."
    return None


def transition_stage_event(
    batch: Batch,
    stage: BatchStage,
    *,
    timestamp: Optional[datetime] = None,
    dst_vessel: Optional[Vessel] = None,
    notes: str = "",
) -> BatchStageEvent:
    """
    Move a batch through one step of the workflow - the only place stage events
    are created. Enforces the workflow (Pitch first and once; each stage starts
    from the batch's current state, Racking also repeatable during Aging;
    nothing after Complete Batch; timestamp not in the future or before the
    latest stage), then in one transaction:
      * transferring stages (Racking, any Filtering) -> Batch.transfer() into dst_vessel
      * Complete Batch -> Batch.complete()
      * add_batch_log() - the BatchStageEvent, with the vessel the batch is in afterwards
      * add_activity_log() - one ActivityLog entry covering the stage (and any transfer)
    Raises ValidationError (user-readable) and saves nothing if the step isn't allowed.
    """
    timestamp = timestamp or timezone.now()
    notes = (notes or "").strip()[:250]
    logger.debug(
        f"transition_stage_event: batch={batch.pk} '{batch.name}' stage={stage.shortid!r} "
        f"at {timestamp} dst={getattr(dst_vessel, 'pk', None)} notes={notes!r}"
    )

    problem = _stage_problem(batch, stage, timestamp, dst_vessel)
    if problem:
        logger.error(f"transition_stage_event: rejected for batch {batch.pk} - {problem}")
        raise ValidationError(problem)

    src_vessel = batch.current_vessel
    try:
        with transaction.atomic():
            if stage.transfers_batch:
                batch.transfer(src_vessel, dst_vessel, timestamp=timestamp, log_activity=False)
            if stage.to_state == BatchStage.STATE_COMPLETED:
                batch.complete(timestamp=timestamp)
            vessel = batch.current_vessel
            event = add_batch_log(batch, stage, timestamp=timestamp, vessel=vessel, notes=notes)

            where = f"[{src_vessel.name}] -> [{vessel.name}]" if stage.transfers_batch else f"[{vessel.name}]"
            text = f"Stage [{stage.name}] :: {where}" + (f" :: {notes}" if notes else "")
            add_activity_log(batch, text, timestamp=timestamp)
    except ValidationError:
        # e.g. transfer() rejecting the destination vessel; nothing was saved.
        batch.refresh_from_db()
        raise
    except Exception:
        batch.refresh_from_db()
        logger.exception(f"transition_stage_event: failed for batch {batch.pk} stage {stage.shortid!r}")
        raise

    logger.info(f"Batch '{batch.name}' -> {stage.name} ({stage.to_state}) in '{vessel.name}'")
    return event


def transfer_batch(
    batch: Batch,
    dst_vessel: Vessel,
    *,
    reason: str,
    stage: Optional[BatchStage] = None,
    timestamp: Optional[datetime] = None,
) -> BatchStageEvent:
    """
    Ad-hoc transfer OUTSIDE the normal workflow (e.g. a vessel has to go Out of
    Service mid-batch). The reason is required.
      * stage=None: move the batch only - Batch.transfer() (its ActivityLog entry
        carries the reason) plus a stage-less BatchStageEvent, so time-in-vessel
        stays right while the batch's stage, allowed next stages and full aging
        don't change.
      * stage=<an allowed next stage that transfers the batch>: log that stage via
        transition_stage_event(), with the reason as its notes.
    Same timestamp rules as stages. Raises ValidationError and saves nothing otherwise.
    """
    timestamp = timestamp or timezone.now()
    reason = (reason or "").strip()[:250]
    logger.debug(
        f"transfer_batch: batch={batch.pk} '{batch.name}' dst={getattr(dst_vessel, 'pk', None)} "
        f"stage={getattr(stage, 'shortid', None)!r} at {timestamp} reason={reason!r}"
    )

    problem = None
    if not reason:
        problem = "A reason is required to transfer a batch outside the workflow."
    elif not batch.active or batch.current_state == BatchStage.STATE_COMPLETED:
        problem = f"Batch '{batch.name}' is complete and can't be transferred."
    elif stage is not None and not stage.transfers_batch:
        problem = f"{stage.name} doesn't transfer the batch - choose no stage change or a stage that does."
    elif dst_vessel is None:
        problem = "Choose a clean, ready destination vessel."
    if problem:
        logger.error(f"transfer_batch: rejected for batch {batch.pk} - {problem}")
        raise ValidationError(problem)

    if stage is not None:
        return transition_stage_event(batch, stage, timestamp=timestamp, dst_vessel=dst_vessel, notes=reason)

    problem = _timestamp_problem(batch, timestamp, "A transfer")
    if problem:
        logger.error(f"transfer_batch: rejected for batch {batch.pk} - {problem}")
        raise ValidationError(problem)

    src_vessel = batch.current_vessel
    try:
        with transaction.atomic():
            batch.transfer(src_vessel, dst_vessel, timestamp=timestamp, reason=reason)
            event = add_batch_log(batch, None, timestamp=timestamp, vessel=dst_vessel, notes=reason)
    except ValidationError:
        batch.refresh_from_db()
        raise
    except Exception:
        batch.refresh_from_db()
        logger.exception(f"transfer_batch: failed for batch {batch.pk}")
        raise

    logger.info(f"Batch '{batch.name}' transferred (no stage change) from '{src_vessel.name}' to '{dst_vessel.name}': {reason!r}")
    return event


# ---------- Vessel create / edit ----------

# The vessel type is the wrapper row linked to the Vessel (see Vessel.vessel_type).
VESSEL_TYPES = {"Fermenter": Fermenter, "Aging Tank": AgingTank, "Barrel": Barrel}


def vessel_name_problem(name: str, exclude_pk: Optional[int] = None) -> Optional[str]:
    """Vessel names are unique ignoring case - pickers and the timeline identify vessels by name."""
    existing = Vessel.objects.filter(name__iexact=name.strip()).exclude(pk=exclude_pk).first()
    return f"A vessel named '{existing.name}' already exists." if existing else None


def _save_type_details(vessel: Vessel, vessel_type: str, *, last_passivation, serial: str, toast_level: str) -> None:
    if vessel_type == "Fermenter":
        Fermenter.objects.update_or_create(vessel=vessel, defaults={"last_passivation": last_passivation})
    elif vessel_type == "Barrel":
        Barrel.objects.update_or_create(vessel=vessel, defaults={"serial": serial, "toastLevel": toast_level})
    elif vessel_type == "Aging Tank":
        AgingTank.objects.get_or_create(vessel=vessel)


def create_vessel(
    *,
    name: str,
    vessel_type: str,
    capacity,
    intended_use: str,
    fill=None,
    last_passivation: Optional[date] = None,
    serial: str = "",
    toast_level: str = "",
) -> Vessel:
    """
    Add a vessel: the Vessel, its type row (Fermenter / Aging Tank / Barrel) and
    its first status history entry - always Clean/Ready. One transaction.
    capacity/fill are volumes ("6 gallons" or a Quantity), stored in liters.
    """
    name = name.strip()
    logger.debug(f"create_vessel: {name!r} type={vessel_type!r} capacity={capacity} fill={fill}")
    problem = None
    if vessel_type not in VESSEL_TYPES:
        problem = f"Unknown vessel type {vessel_type!r}."
    else:
        problem = vessel_name_problem(name)
    if problem:
        logger.error(f"create_vessel: rejected - {problem}")
        raise ValidationError(problem)

    with transaction.atomic():
        vessel = Vessel.objects.create(
            name=name, capacity=capacity, fill=fill, intended_use=intended_use, status=Vessel.STATUS_READY,
        )
        _save_type_details(vessel, vessel_type, last_passivation=last_passivation, serial=serial, toast_level=toast_level)
        set_vessel_status(vessel, Vessel.STATUS_READY, notes="Vessel added")
    logger.info(f"Vessel '{vessel.name}' ({vessel_type}) added")
    return vessel


def update_vessel(
    vessel: Vessel,
    *,
    name: str,
    capacity,
    intended_use: str,
    fill=None,
    last_passivation: Optional[date] = None,
    serial: str = "",
    toast_level: str = "",
) -> Vessel:
    """
    Edit a vessel's details and its type's details. Never changes the type or
    the status (status only moves through the vessel actions and transfers).
    """
    name = name.strip()
    logger.debug(f"update_vessel: {vessel.pk} {vessel.name!r} -> {name!r} capacity={capacity} fill={fill}")
    problem = vessel_name_problem(name, exclude_pk=vessel.pk)
    if problem:
        logger.error(f"update_vessel: rejected for vessel {vessel.pk} - {problem}")
        raise ValidationError(problem)

    vessel_type = vessel.vessel_type
    with transaction.atomic():
        vessel.name, vessel.capacity, vessel.fill, vessel.intended_use = name, capacity, fill, intended_use
        vessel.save(update_fields=["name", "capacity", "fill", "intended_use"])
        _save_type_details(vessel, vessel_type, last_passivation=last_passivation, serial=serial, toast_level=toast_level)
    vessel.refresh_from_db()
    logger.info(f"Vessel {vessel.pk} '{vessel.name}' updated")
    return vessel
