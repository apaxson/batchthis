import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from math import floor, inf
from typing import Optional

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    ActivityLog,
    AgingTank,
    Barrel,
    Batch,
    BatchPlanStep,
    BatchStage,
    BatchStageEvent,
    Fermenter,
    PairingTag,
    PlanStep,
    Recipe,
    RecipePlanStep,
    Vessel,
    Span,
    VesselStatusEvent,
    VesselStay,
    WorkflowTemplate,
    WorkflowTemplateStep,
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
    packaging: str = "",
    plan_step: Optional[BatchPlanStep] = None,
) -> BatchStageEvent:
    """Write one row of the batch's stage log. No workflow checks - see transition_stage_event()."""
    event = BatchStageEvent.objects.create(
        batch=batch, stage=stage, timestamp=timestamp, vessel=vessel, notes=notes,
        packaging=packaging, plan_step=plan_step,
    )
    logger.debug(f"add_batch_log: batch={batch.pk} stage={getattr(stage, 'shortid', None)!r} vessel={getattr(vessel, 'pk', None)} at {timestamp}")
    return event


def add_activity_log(batch: Batch, text: str, *, timestamp: Optional[datetime] = None) -> ActivityLog:
    """Add one entry to the batch's ActivityLog."""
    entry = ActivityLog.objects.create(datetime=timestamp or timezone.now(), text=text)
    batch.activity.add(entry)
    logger.debug(f"add_activity_log: batch={batch.pk} {text!r}")
    return entry


# Edit batch's fields as they're labelled on the form, in form order.
BATCH_EDIT_LABELS = (
    ('name', "Name"),
    ('recipe', "Recipe"),
    ('size', "Batch Size"),
    ('startingGravity', "Starting Gravity"),
    ('estimatedEndGravity', "Estimated End Gravity"),
)


def _batch_edit_display(batch: Batch) -> dict[str, str]:
    """The edited fields as shown in the log - also what decides whether a value really changed."""
    return {
        'name': batch.name,
        'recipe': str(batch.recipe) if batch.recipe else "No recipe",
        'size': f"{batch.size.magnitude:.2f} {batch.size.units}",
        'startingGravity': f"{batch.startingGravity.magnitude:.3f}",
        'estimatedEndGravity': f"{batch.estimatedEndGravity.magnitude:.3f}",
    }


def save_batch_edit(
    batch: Batch,
    *,
    name: str,
    recipe: Optional[Recipe],
    size,
    starting_gravity: float,
    estimated_end_gravity: float,
) -> list[str]:
    """
    Save Edit batch's fields and log ONE "Batch edited" entry listing what actually
    changed ("Name [Old] -> [New]"; a value re-entered differently, e.g. "6 gallons"
    for 6.00 gallon, isn't a change). Save and log commit together, or neither does.
    Returns the changes (empty = nothing logged). Only these fields are written -
    never vessel/fermenter/startdate/active.
    """
    from pint import Quantity

    before = _batch_edit_display(batch)
    batch.name = name
    batch.recipe = recipe
    batch.size = size
    batch.startingGravity = Quantity(starting_gravity, 'sg')
    batch.estimatedEndGravity = Quantity(estimated_end_gravity, 'sg')
    after = _batch_edit_display(batch)
    changes = [f"{label} [{before[field]}] -> [{after[field]}]"
               for field, label in BATCH_EDIT_LABELS if before[field] != after[field]]
    logger.debug(f"save_batch_edit: batch={batch.pk} changes={changes}")

    with transaction.atomic():
        batch.save(update_fields=[field for field, _label in BATCH_EDIT_LABELS])
        if changes:
            add_activity_log(batch, "Batch edited :: " + "; ".join(changes))
    logger.info(f"Batch '{batch.name}' edited: {'; '.join(changes) or 'no changes'}")
    return changes


def _workflow_problem(batch: Batch, stage: BatchStage, current: Optional[BatchStageEvent]) -> Optional[str]:
    """Why the workflow doesn't allow this stage next (ignoring timestamp/vessel), or None."""
    state = current.stage.to_state if current else None
    if state == BatchStage.STATE_COMPLETED or not batch.active:
        return f"Batch '{batch.name}' is complete; no more stages can be logged."
    if stage.can_follow(state):
        return None
    if current is None:
        return f"Pitch the batch first - '{batch.name}' hasn't been pitched yet."
    return f"{stage.name} needs a batch in {stage.get_from_state_display()}; '{batch.name}' is in {state}."


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
    # By local date, not time: a batch created today has startdate = the moment it was saved, so a
    # stage logged earlier that day (or at the form's minute-rounded default time) is still valid.
    if timezone.localtime(timestamp).date() < timezone.localtime(batch.startdate).date():
        return f"{what} can't be before the batch's start date ({timezone.localtime(batch.startdate):%b %d, %Y})."
    return None


def _stage_problem(batch: Batch, stage: BatchStage, timestamp: datetime, dst_vessel: Optional[Vessel],
                   packaging: str = "") -> Optional[str]:
    """Why this stage can't be logged on this batch right now, or None if it can."""
    current = batch.current_stage_event
    problem = _workflow_problem(batch, stage, current)
    if problem:
        return problem

    problem = _timestamp_problem(batch, timestamp, stage.name)
    if problem:
        return problem

    if packaging:
        if packaging not in dict(PlanStep.PACKAGING_CHOICES):
            return "Choose Bottles or Kegs."
        if stage.to_state != BatchStage.STATE_BOTTLING:
            return f"Only Sterile Filtering can package the batch - {stage.name} can't."
        if dst_vessel is not None:
            return "Choose a destination vessel or Bottles / Kegs, not both."
        return None
    if stage.transfers_batch and dst_vessel is None:
        if stage.to_state == BatchStage.STATE_BOTTLING:
            return f"{stage.name} transfers the batch - choose a destination vessel or Bottles / Kegs."
        return f"{stage.name} transfers the batch - choose a destination vessel."
    if not stage.transfers_batch and dst_vessel is not None:
        return f"{stage.name} doesn't transfer the batch - don't choose a destination vessel."
    return None


def _next_plan_step(batch: Batch, stage: BatchStage) -> Optional[BatchPlanStep]:
    """
    The planned step a newly logged `stage` fulfils: the first planned step with
    that stage AFTER the last step already fulfilled. Steps passed over are then
    Skipped (plan_progress). None = unplanned (e.g. an extra racking), or no plan.
    """
    last = (batch.stage_events.filter(plan_step__isnull=False).select_related('plan_step')
            .order_by('-plan_step__sort_order').first())
    after = last.plan_step.sort_order if last else 0
    step = batch.plan_steps.filter(stage=stage, sort_order__gt=after).order_by('sort_order').first()
    logger.debug(f"_next_plan_step: batch={batch.pk} stage={stage.shortid!r} after step {after} -> "
                 f"{step.sort_order if step else None}")
    return step


def transition_stage_event(
    batch: Batch,
    stage: BatchStage,
    *,
    timestamp: Optional[datetime] = None,
    dst_vessel: Optional[Vessel] = None,
    packaging: str = "",
    notes: str = "",
) -> BatchStageEvent:
    """
    Move a batch through one step of the workflow - the only place stage events
    are created. Enforces the workflow (Pitch first and once; each stage starts
    from the batch's current state, Racking also repeatable during Aging;
    nothing after Complete Batch; timestamp not in the future or before the
    latest stage), then in one transaction:
      * transferring stages (Racking, any Filtering) -> Batch.transfer() into dst_vessel,
        or - Sterile Filtering only - Batch.package() into Bottles / Kegs (packaging)
      * Complete Batch -> Batch.complete()
      * add_batch_log() - the BatchStageEvent, with the vessel the batch is in afterwards
        (None once packaged) and the planned step it fulfils (_next_plan_step)
      * add_activity_log() - one ActivityLog entry covering the stage (and any transfer)
    Raises ValidationError (user-readable) and saves nothing if the step isn't allowed.
    """
    timestamp = timestamp or timezone.now()
    notes = (notes or "").strip()[:250]
    packaging = (packaging or "").strip()
    logger.debug(
        f"transition_stage_event: batch={batch.pk} '{batch.name}' stage={stage.shortid!r} "
        f"at {timestamp} dst={getattr(dst_vessel, 'pk', None)} packaging={packaging!r} notes={notes!r}"
    )

    problem = _stage_problem(batch, stage, timestamp, dst_vessel, packaging)
    if problem:
        logger.error(f"transition_stage_event: rejected for batch {batch.pk} - {problem}")
        raise ValidationError(problem)

    src_vessel = batch.current_vessel
    plan_step = _next_plan_step(batch, stage)
    try:
        with transaction.atomic():
            if packaging:
                batch.package(src_vessel, packaging, timestamp=timestamp)
            elif stage.transfers_batch:
                batch.transfer(src_vessel, dst_vessel, timestamp=timestamp, log_activity=False)
            if stage.to_state == BatchStage.STATE_COMPLETED:
                batch.complete(timestamp=timestamp)
            vessel = batch.current_vessel
            event = add_batch_log(batch, stage, timestamp=timestamp, vessel=vessel, notes=notes,
                                  packaging=packaging, plan_step=plan_step)

            here = f"[{vessel.name}]" if vessel else batch.packaging   # packaged: "Bottles" / "Kegs"
            where = f"[{src_vessel.name}] -> {here}" if stage.transfers_batch else here
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

    logger.info(f"Batch '{batch.name}' -> {stage.name} ({stage.to_state}) in "
                f"{repr(vessel.name) if vessel else batch.packaging}"
                + (f", planned step {plan_step.sort_order}" if plan_step else ", unplanned"))
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
    elif batch.is_packaged:
        problem = f"Batch '{batch.name}' is packaged in {batch.packaging} - there's no vessel to transfer it from."
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
VESSEL_TYPES = {Vessel.TYPE_FERMENTER: Fermenter, Vessel.TYPE_AGING_TANK: AgingTank, Vessel.TYPE_BARREL: Barrel}


def vessel_name_problem(name: str, exclude_pk: Optional[int] = None) -> Optional[str]:
    """Vessel names are unique ignoring case - pickers and the timeline identify vessels by name."""
    existing = Vessel.objects.filter(name__iexact=name.strip()).exclude(pk=exclude_pk).first()
    return f"A vessel named '{existing.name}' already exists." if existing else None


def _save_type_details(vessel: Vessel, vessel_type: str, *, last_passivation, serial: str, toast_level: str) -> None:
    if vessel_type == Vessel.TYPE_FERMENTER:
        Fermenter.objects.update_or_create(vessel=vessel, defaults={"last_passivation": last_passivation})
    elif vessel_type == Vessel.TYPE_BARREL:
        Barrel.objects.update_or_create(vessel=vessel, defaults={"serial": serial, "toastLevel": toast_level})
    elif vessel_type == Vessel.TYPE_AGING_TANK:
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


# ---------- Plans: workflow templates and recipes (phase 2) ----------

_UNCHECKED = object()  # vessel type not given - check order/durations only


def _plan_items(steps) -> list[tuple]:
    """
    (stage, planned days or None, vessel type) per step. Accepts saved PlanStep
    rows, (stage, duration, vessel type) triples, or (stage, duration) pairs -
    pairs skip the vessel type checks (e.g. totals). Duration as text ("2 weeks"),
    a Quantity or None; 0 counts as None.
    """
    ureg = settings.DJANGO_PINT_UNIT_REGISTER
    items = []
    for step in steps:
        if isinstance(step, PlanStep):
            items.append((step.stage, step.planned_days, step.vessel_type))
            continue
        stage, duration, *rest = step
        days = None
        if duration is not None:
            quantity = ureg.Quantity(duration) if isinstance(duration, str) else duration
            days = quantity.to('day').magnitude or None
        items.append((stage, days, rest[0] if rest else _UNCHECKED))
    return items


def allowed_vessel_types(stage: BatchStage) -> list[str]:
    """
    The vessel types a plan step for this stage may name (Aaron, 2026-09-24):
    Pitch always starts in a Fermenter, Complete Batch stays put (None / Current),
    and every other stage - they all move the batch - needs a real vessel type.
    Sterile Filtering (into Bottling) may also plan Bottles or Kegs.
    """
    if stage.from_state == "":  # Pitch
        return [Vessel.TYPE_FERMENTER]
    if stage.to_state == BatchStage.STATE_COMPLETED:
        return [PlanStep.VESSEL_CURRENT]
    allowed = [value for value, _ in Vessel.TYPE_CHOICES]
    if stage.to_state == BatchStage.STATE_BOTTLING:  # Sterile Filtering
        allowed += [value for value, _ in PlanStep.PACKAGING_CHOICES]
    return allowed if stage.transfers_batch else allowed + [PlanStep.VESSEL_CURRENT]


def _vessel_type_problem(stage: BatchStage, vessel_type) -> str | None:
    """Why this vessel type doesn't fit the stage (see allowed_vessel_types()), or None."""
    if vessel_type is _UNCHECKED:
        return None
    if vessel_type not in {value for value, _ in PlanStep.VESSEL_TYPE_CHOICES}:
        return "choose a vessel type."
    allowed = allowed_vessel_types(stage)
    if vessel_type in allowed:
        return None
    if stage.from_state == "":  # Pitch
        return f"{stage.name} always starts in a Fermenter."
    if stage.to_state == BatchStage.STATE_COMPLETED:
        return f"{stage.name} ends the batch - its vessel type is None / Current."
    if vessel_type in dict(PlanStep.PACKAGING_CHOICES):
        return f"{stage.name} can't move the batch into {vessel_type} - only Sterile Filtering can."
    labels = [label for value, label in PlanStep.VESSEL_TYPE_CHOICES if value in allowed]
    return f"{stage.name} moves the batch - choose {', '.join(labels[:-1])} or {labels[-1]}."


def plan_step_problems(steps) -> list[tuple[int, str]]:
    """
    Why a per-step plan isn't valid, as (step number, message) pairs - so a step
    editor can show each message on its own row. Empty = valid.
    Every step - with or without a duration - must follow the workflow order
    (BatchStage.can_follow(), the same rule the live workflow uses). Durations are
    optional, except Complete Batch ends the batch and can't have one. Each step
    needs a vessel type: Pitch is always Fermenter, steps that move the batch need
    a real type, Complete Batch is always None / Current.
    """
    problems = []
    state, previous = None, None
    for number, (stage, days, vessel_type) in enumerate(_plan_items(steps), start=1):
        if state == BatchStage.STATE_COMPLETED:
            problems.append((number, "nothing can come after Complete Batch."))
            break
        if not stage.can_follow(state):
            if state is None:
                problems.append((number, "the plan must start with Pitch."))
            else:
                problems.append((number, (
                    f"{stage.name} can't come after {previous.name} - the batch would be in "
                    f"{state}, and {stage.name} needs {stage.get_from_state_display()}."
                )))
        if days is not None and stage.to_state == BatchStage.STATE_COMPLETED:
            problems.append((number, f"{stage.name} ends the batch - leave its duration blank."))
        vessel_problem = _vessel_type_problem(stage, vessel_type)
        if vessel_problem:
            problems.append((number, vessel_problem))
        state, previous = stage.to_state, stage
    logger.debug(f"plan_step_problems: {len(problems)} problem(s): {problems}")
    return problems


def plan_problems(steps) -> list[str]:
    """plan_step_problems() as one list of "Step N: ..." messages (empty = valid)."""
    return [f"Step {number}: {message}" for number, message in plan_step_problems(steps)]


@dataclass(frozen=True)
class PlanTotals:
    total_days: float
    by_state: dict  # {BatchStage.STATE_*: days}, only states with timed steps


def plan_totals(steps) -> PlanTotals:
    """
    Planned time, summing ONLY steps with a duration (blank/0 = point-in-time,
    skipped). Each step's time counts toward the state it moves the batch into:
    Pitch -> Fermentation, Racking/Filtering -> Aging, Sterile Filtering -> Bottling.
    """
    by_state: dict = {}
    for stage, days, _vessel_type in _plan_items(steps):
        if days:
            by_state[stage.to_state] = by_state.get(stage.to_state, 0) + days
    return PlanTotals(total_days=sum(by_state.values()), by_state=by_state)


def copy_template_to_recipe(template: WorkflowTemplate, recipe: Recipe) -> list[RecipePlanStep]:
    """
    Give the recipe its OWN copy of the template's steps (replacing any plan it
    had) and remember the template for reference. Later template edits don't
    change the recipe, and the recipe's steps can be adjusted freely.
    """
    logger.debug(f"copy_template_to_recipe: template={template.pk} '{template}' -> recipe={recipe.pk} '{recipe}'")
    with transaction.atomic():
        recipe.plan_steps.all().delete()
        copies = RecipePlanStep.objects.bulk_create([
            RecipePlanStep(recipe=recipe, sort_order=step.sort_order, stage=step.stage,
                           planned_duration=step.planned_duration, vessel_type=step.vessel_type, notes=step.notes)
            for step in template.steps.all()
        ])
        recipe.workflow_template = template
        recipe.save(update_fields=['workflow_template'])
    logger.info(f"Recipe '{recipe}' plan copied from template '{template}' ({len(copies)} steps)")
    return copies


def workflow_name_problem(name: str, exclude_pk: int | None = None) -> str | None:
    """Workflow template names are unique, ignoring case (like vessel names)."""
    clash = WorkflowTemplate.objects.filter(name__iexact=name.strip())
    if exclude_pk is not None:
        clash = clash.exclude(pk=exclude_pk)
    existing = clash.first()
    return f"A workflow named '{existing.name}' already exists." if existing else None


def _plan_rows_problems(rows: list[dict]) -> list[str]:
    """Why step rows (dicts of stage, planned_duration, vessel_type, notes) can't be saved as a plan."""
    problems = [] if rows else ["Add at least one step."]
    return problems + plan_problems([(r['stage'], r['planned_duration'], r['vessel_type']) for r in rows])


def _replace_plan_steps(step_model, owner_field: str, owner, rows: list[dict]) -> list:
    """Replace the owner's plan steps with `rows`, numbered in the order given. Call inside a transaction."""
    step_model.objects.filter(**{owner_field: owner}).delete()
    return step_model.objects.bulk_create([
        step_model(**{owner_field: owner}, sort_order=order, stage=row['stage'],
                   planned_duration=row['planned_duration'], vessel_type=row['vessel_type'],
                   notes=row.get('notes', ''))
        for order, row in enumerate(rows, start=1)
    ])


def save_workflow_template(template: WorkflowTemplate | None, *, name: str, description: str,
                           rows: list[dict]) -> WorkflowTemplate:
    """
    Create (template=None) or update a workflow template and REPLACE its steps
    with `rows` - dicts of stage, planned_duration, vessel_type, notes - in the
    order given. The plan must be valid (plan_problems) and have at least one
    step; otherwise ValidationError and nothing is saved. Recipes that copied
    the template keep their own steps.
    """
    logger.debug(f"save_workflow_template: template={template.pk if template else None} name={name!r} "
                 f"{len(rows)} step(s)")
    problems = []
    name_problem = workflow_name_problem(name, exclude_pk=template.pk if template else None)
    if name_problem:
        problems.append(name_problem)
    problems += _plan_rows_problems(rows)
    if problems:
        logger.debug(f"save_workflow_template: rejected: {problems}")
        raise ValidationError(problems)

    with transaction.atomic():
        template = template or WorkflowTemplate()
        template.name, template.description = name.strip(), description.strip()
        template.save()
        _replace_plan_steps(WorkflowTemplateStep, 'template', template, rows)
    logger.info(f"Workflow template '{template}' saved with {len(rows)} step(s)")
    return template


def save_recipe_plan(recipe: Recipe, rows: list[dict], template: WorkflowTemplate | None = None) -> list[RecipePlanStep]:
    """
    REPLACE the recipe's own plan with `rows` (same shape and rules as
    save_workflow_template). `template` is the workflow the rows were started
    from, recorded as "Copied from"; None keeps whatever the recipe had.
    Invalid -> ValidationError and the old plan stays.
    """
    logger.debug(f"save_recipe_plan: recipe={recipe.pk} '{recipe}' {len(rows)} step(s), "
                 f"template={template.pk if template else None}")
    problems = _plan_rows_problems(rows)
    if problems:
        logger.debug(f"save_recipe_plan: rejected: {problems}")
        raise ValidationError(problems)

    with transaction.atomic():
        steps = _replace_plan_steps(RecipePlanStep, 'recipe', recipe, rows)
        if template is not None:
            recipe.workflow_template = template
            recipe.save(update_fields=['workflow_template'])
    logger.info(f"Recipe '{recipe}' plan saved with {len(steps)} step(s)"
                + (f", copied from '{template}'" if template else ""))
    return steps


def clear_recipe_plan(recipe: Recipe) -> None:
    """A plan is optional: remove the recipe's steps and its "Copied from" reference."""
    with transaction.atomic():
        count, _ = recipe.plan_steps.all().delete()
        recipe.workflow_template = None
        recipe.save(update_fields=['workflow_template'])
    logger.info(f"Recipe '{recipe}' plan cleared ({count} step(s) removed)")


def set_recipe_pairings(recipe: Recipe, names: list[str]) -> list[PairingTag]:
    """
    Make `names` (already cleaned - forms.PairingTagsField) the recipe's food pairings.
    An existing tag is reused whatever its case ("aged cheddar" -> "Aged Cheddar");
    a new name becomes a new tag. Tags a recipe stops using are kept (Aaron, 2026-09-25).
    """
    logger.debug(f"set_recipe_pairings: recipe={recipe.pk} names={names}")
    with transaction.atomic():
        tags = []
        for name in names:
            tag = PairingTag.objects.filter(name__iexact=name).first()
            if tag is None:
                tag = PairingTag.objects.create(name=name)
                logger.info(f"New pairing tag '{tag}'")
            tags.append(tag)
        recipe.pairings.set(tags)
    logger.info(f"Recipe '{recipe}' pairings: {', '.join(t.name for t in tags) or 'none'}")
    return tags


def _step_rows(steps) -> list[dict]:
    """Saved plan steps as rows for _replace_plan_steps() - to copy a plan from one owner to another."""
    return [{'stage': s.stage, 'planned_duration': s.planned_duration, 'vessel_type': s.vessel_type, 'notes': s.notes}
            for s in steps]


def copy_plan_to_batch(batch: Batch, template: WorkflowTemplate | None = None) -> list[BatchPlanStep]:
    """
    Give a new batch its OWN copy of a plan (Aaron, 2026-09-24): its recipe's
    plan if the recipe has one (and the recipe's "Copied from" workflow), otherwise
    `template`, which is then required. Later recipe/template edits don't change it.
    """
    recipe = batch.recipe
    recipe_steps = list(recipe.plan_steps.select_related('stage')) if recipe else []
    logger.debug(f"copy_plan_to_batch: batch={batch.pk} recipe={recipe.pk if recipe else None} "
                 f"({len(recipe_steps)} steps) template={template.pk if template else None}")
    if recipe_steps:
        source, copied_from = recipe_steps, recipe.workflow_template
    elif template is not None:
        source, copied_from = list(template.steps.select_related('stage')), template
    else:
        logger.debug(f"copy_plan_to_batch: batch {batch.pk} rejected - no recipe plan and no workflow")
        raise ValidationError("This recipe has no plan - choose a workflow.")

    with transaction.atomic():
        steps = _replace_plan_steps(BatchPlanStep, 'batch', batch, _step_rows(source))
        batch.workflow_template = copied_from
        batch.save(update_fields=['workflow_template'])
    logger.info(f"Batch '{batch}' plan copied from {'recipe' if recipe_steps else 'workflow'} "
                f"({len(steps)} steps)")
    return steps


def batch_plan_locked_reason(batch: Batch) -> str | None:
    """Why the batch's plan can't be edited, or None. Editable until Pitch (Aaron, 2026-09-24)."""
    if not batch.plan_steps.exists():
        return "This batch has no plan."
    if batch.current_state is not None:
        return "The plan can't be changed after Pitch."
    return None


def save_batch_plan(batch: Batch, rows: list[dict]) -> list[BatchPlanStep]:
    """
    REPLACE an unpitched batch's plan with `rows` (same shape and rules as
    save_recipe_plan). Rejected with ValidationError once the batch is pitched, or
    if it has no plan (batches created before batch plans stay without one).
    """
    logger.debug(f"save_batch_plan: batch={batch.pk} '{batch}' {len(rows)} step(s)")
    locked = batch_plan_locked_reason(batch)
    problems = [locked] if locked else _plan_rows_problems(rows)
    if problems:
        logger.debug(f"save_batch_plan: rejected: {problems}")
        raise ValidationError(problems)
    with transaction.atomic():
        steps = _replace_plan_steps(BatchPlanStep, 'batch', batch, rows)
    logger.info(f"Batch '{batch}' plan saved with {len(steps)} step(s)")
    return steps


def delete_workflow_template(template: WorkflowTemplate) -> None:
    """Delete a template; recipes that copied it keep their steps (Recipe.workflow_template is SET_NULL)."""
    name, recipes = template.name, template.recipes.count()
    template.delete()
    logger.info(f"Workflow template '{name}' deleted ({recipes} recipe(s) keep their copied steps)")


# ---------- Plan vs actual (step 11) ----------

@dataclass(frozen=True)
class PlanProgressRow:
    """
    One row of a batch's plan vs actual. `step` is the planned step (None for an
    unplanned stage), `event` the logged stage (None if not logged yet / skipped).
    Days are counted from Pitch; *_days are time spent in the step.
    """
    status: str                 # done / current / skipped / upcoming / unplanned
    stage: BatchStage
    step: Optional[BatchPlanStep]
    event: Optional[BatchStageEvent]
    planned_day: Optional[float]
    actual_day: Optional[float]
    planned_days: Optional[float]
    actual_days: Optional[float]
    planned_vessel: str         # the planned vessel type's label, "" if unplanned
    actual_vessel: str          # the vessel the batch went into, or Bottles / Kegs

    @property
    def difference_days(self) -> Optional[float]:
        """Actual minus planned time in the step (+ = longer than planned)."""
        if self.planned_days is None or self.actual_days is None:
            return None
        return round(self.actual_days - self.planned_days, 1)


def _days(delta) -> float:
    return round(delta.total_seconds() / 86400, 1)


def plan_progress(batch: Batch) -> list[PlanProgressRow]:
    """
    The batch's plan vs what actually happened, in the order it happened (Aaron,
    2026-09-24): each planned step with its planned start day and time, next to the
    logged stage that fulfilled it (actual day, time spent, vessel or packaging).
    Planned steps passed over are Skipped; stages not in the plan are Unplanned,
    shown where they happened. Transfers aren't plan steps. Empty for a batch
    without a plan.
    """
    steps = list(batch.plan_steps.select_related('stage'))
    if not steps:
        return []
    events = [e for e in batch.stage_events.select_related('stage', 'vessel', 'plan_step').order_by('timestamp', 'pk')
              if e.stage is not None]
    pitched_at = events[0].timestamp if events else None
    now = timezone.now()

    planned_day, day = {}, 0.0
    for step in steps:
        planned_day[step.pk] = round(day, 1)
        day += step.planned_days or 0

    def actual(event, i):
        if event.stage.to_state == BatchStage.STATE_COMPLETED:
            spent = None                                   # Complete Batch ends the batch
        else:
            end = events[i + 1].timestamp if i + 1 < len(events) else (None if not batch.active else now)
            spent = _days(end - event.timestamp) if end else None
        where = event.vessel.name if event.vessel else event.get_packaging_display()
        return _days(event.timestamp - pitched_at), spent, where

    def planned_row(step, status, event=None, i=None):
        actual_day, actual_days, actual_vessel = actual(event, i) if event else (None, None, "")
        return PlanProgressRow(
            status=status, stage=step.stage, step=step, event=event,
            planned_day=planned_day[step.pk], actual_day=actual_day,
            planned_days=round(step.planned_days, 1) if step.planned_days else None, actual_days=actual_days,
            planned_vessel=step.get_vessel_type_display(), actual_vessel=actual_vessel,
        )

    rows, emitted = [], set()
    latest = len(events) - 1
    for i, event in enumerate(events):
        step = event.plan_step
        if step is None or step.pk not in planned_day:
            actual_day, actual_days, where = actual(event, i)
            rows.append(PlanProgressRow(
                status='unplanned', stage=event.stage, step=None, event=event, planned_day=None,
                actual_day=actual_day, planned_days=None, actual_days=actual_days,
                planned_vessel="", actual_vessel=where,
            ))
            continue
        for earlier in steps:                                # planned steps passed over
            if earlier.sort_order < step.sort_order and earlier.pk not in emitted:
                rows.append(planned_row(earlier, 'skipped'))
                emitted.add(earlier.pk)
        is_current = i == latest and batch.active
        rows.append(planned_row(step, 'current' if is_current else 'done', event, i))
        emitted.add(step.pk)
    rows += [planned_row(step, 'upcoming') for step in steps if step.pk not in emitted]
    logger.debug(f"plan_progress: batch={batch.pk} {len(steps)} planned steps, {len(events)} stages -> "
                 f"{[(r.stage.shortid, r.status) for r in rows]}")
    return rows


# ---------- Schedule progress: the batch page's progress bar (TODO.txt PROGRESS BAR) ----------

PROGRESS_MIN_WIDTH = 6.0  # % of the bar - a short stage stays wide enough to read
PROGRESS_STEP_GAP = 14.0  # % of the bar a step label needs before the next one on its row


@dataclass(frozen=True)
class ProgressStage:
    """
    One stage of the progress bar's input. Kept generic (a name, not a BatchStage
    state) so DYNAMIC WORKFLOWS phase 5 only swaps progress_stages() for the
    batch's workflow definition. actual_days: time spent (done), so far
    (current), None (ahead).
    """
    name: str
    status: str                       # done / current / ahead
    planned_days: Optional[float]     # the plan's timed steps in this stage; None = not planned
    actual_days: Optional[float]
    note: str = ""                    # an extra figure under the stage, e.g. "Full aging 38 d"


@dataclass(frozen=True)
class ProgressSegment:
    """A stage drawn on the bar. left/width are % of the bar; fill/overrun are % of the segment."""
    stage: ProgressStage
    expected_days: float
    left: float
    width: float
    fill: float           # current stage: elapsed time, up to its planned time
    overrun: float        # current stage: time past its planned time
    overrun_days: int


@dataclass(frozen=True)
class ProgressStay:
    """Time in one vessel (or Bottles/Kegs) on the bar; left/width/tail are % of the bar."""
    kind: str                         # past / current / planned
    label: str                        # vessel name, Bottles/Kegs, or the plan's vessel type ("Any Aging Tank")
    vessel: Optional[Vessel]
    step: str                         # the step that started it (Pitch, Racking, Transfer, ...)
    left: float
    width: float
    tail: float = 0.0                 # current stay: from today to its planned end (drawn dashed)
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    days: Optional[int] = None        # whole days in the vessel (so far), or planned
    notes: str = ""

    @property
    def right(self) -> float:
        """Where the stay ends on the bar (% of the bar) - and its dashed tail starts."""
        return self.left + self.width


@dataclass(frozen=True)
class ProgressStep:
    """A point-in-time step (Pitch, Racking, a Filtering, Transfer, Complete Batch) as a marker on the bar."""
    name: str
    status: str                       # done / ahead
    at: float                         # % of the bar
    when: Optional[datetime] = None   # when it happened; None ahead
    notes: str = ""
    row: int = 0                      # label row, so labels close together don't overlap


@dataclass(frozen=True)
class ScheduleProgress:
    segments: list
    has_plan: bool
    pitched: bool
    completed: bool
    elapsed_days: int
    expected_total_days: Optional[int]   # None without a plan
    remaining_days: Optional[int]
    percent: Optional[int]               # % of plan; None without a plan
    marker: Optional[float]              # today, % of the bar; None before Pitch and once completed
    started_at: Optional[datetime]
    forecast_end: Optional[datetime]
    completed_at: Optional[datetime]
    stays: list = field(default_factory=list)   # ProgressStay, from batch_schedule_progress()
    steps: list = field(default_factory=list)   # ProgressStep, in bar order

    @property
    def overruns(self) -> list[tuple[str, int]]:
        """(stage name, whole days over plan) for a current stage past its planned time."""
        return [(s.stage.name, s.overrun_days) for s in self.segments if s.overrun_days > 0]


def progress_stages(stays: list[VesselStay], plan_steps, *, full_aging: Optional[Span] = None,
                    now: Optional[datetime] = None) -> list[ProgressStage]:
    """
    The fixed Fermentation -> Aging -> Bottling states as a stage list: planned
    time from the plan's timed steps (plan_totals), actual time = the stage's
    vessel stays added up (an open stay measured to `now`). States passed over
    are left out; states not reached yet are ahead. Aging notes Batch.full_aging().
    """
    now = now or timezone.now()
    planned = plan_totals(plan_steps).by_state
    actual: dict = {}
    current = None
    for stay in stays:
        if stay.state is None or stay.state == BatchStage.STATE_COMPLETED:
            continue  # a transfer before Pitch
        actual[stay.state] = actual.get(stay.state, 0.0) + ((stay.end or now) - stay.start).total_seconds() / 86400
        if stay.is_open:
            current = stay.state
    states = BatchStage.TIMELINE_STATES
    last_reached = max((states.index(state) for state in actual), default=-1)
    stages = []
    for i, state in enumerate(states):
        if state in actual:
            status = 'current' if state == current else 'done'
        elif i > last_reached:
            status = 'ahead'
        else:
            continue
        note = ""
        if state == BatchStage.STATE_AGING and full_aging is not None:
            note = f"Full aging {round(full_aging.duration.total_seconds() / 86400)} d" + (
                " so far" if full_aging.is_open else "")
        stages.append(ProgressStage(state, status, planned.get(state), actual.get(state), note))
    logger.debug(f"progress_stages: {[(s.name, s.status, s.planned_days, s.actual_days) for s in stages]}")
    return stages


def _segment_widths(days: list[float]) -> list[float]:
    """% widths in proportion to `days`, summing to 100; a short stage gets PROGRESS_MIN_WIDTH."""
    count = len(days)
    if not count:
        return []
    fixed: set = set()
    while len(fixed) < count:
        free = 100 - PROGRESS_MIN_WIDTH * len(fixed)
        rest = sum(d for i, d in enumerate(days) if i not in fixed)
        widths = [PROGRESS_MIN_WIDTH if i in fixed else (d / rest * free if rest else free / (count - len(fixed)))
                  for i, d in enumerate(days)]
        short = {i for i, w in enumerate(widths) if w < PROGRESS_MIN_WIDTH} - fixed
        if not short:
            return widths
        fixed |= short
    return [100 / count] * count


def schedule_progress(stages: list[ProgressStage], *, started_at: Optional[datetime],
                      completed_at: Optional[datetime] = None) -> ScheduleProgress:
    """
    The progress bar's numbers and geometry (TODO.txt PROGRESS BAR, Aaron 2026-09-23):
        expected total = actual time of done stages + max(planned, elapsed) of the
                         current stage + planned time of the stages ahead
    % of plan = elapsed / expected total - under 100 until Completed, so a current
    stage past its plan stretches the total day by day. A calculation only: the
    stored plan never changes. Without a plan (no timed steps): elapsed time only,
    no %, remaining or forecast.
    """
    has_plan = any(s.planned_days for s in stages)
    pitched, completed = started_at is not None, completed_at is not None
    shown = stages if has_plan else [s for s in stages if s.status != 'ahead']

    def expected(stage: ProgressStage) -> float:
        if stage.status == 'done':
            return stage.actual_days
        if stage.status == 'current':
            return max(stage.planned_days or 0, stage.actual_days) if has_plan else stage.actual_days
        return stage.planned_days or 0

    days = [expected(s) for s in shown]
    total = sum(days)
    elapsed = sum(s.actual_days for s in shown if s.status != 'ahead')

    segments, left = [], 0.0
    for stage, stage_days, width in zip(shown, days, _segment_widths(days)):
        fill = overrun = 0.0
        overrun_days = 0
        if stage.status == 'current' and stage_days > 0:
            planned = stage.planned_days or 0
            if has_plan and planned and stage.actual_days > planned:
                fill = planned / stage_days * 100
                overrun, overrun_days = 100 - fill, round(stage.actual_days - planned)
            else:
                fill = stage.actual_days / stage_days * 100
        segments.append(ProgressSegment(stage, stage_days, left, width, fill, overrun, overrun_days))
        left += width

    marker = None
    current = next((s for s in segments if s.stage.status == 'current'), None)
    if pitched and not completed and current:
        share = min(current.stage.actual_days / current.expected_days, 1.0) if current.expected_days else 0.0
        marker = current.left + current.width * share

    percent = None
    if has_plan:
        if completed:
            percent = 100
        elif not pitched or not total:
            percent = 0
        else:
            percent = min(floor(elapsed / total * 100), 99)
    total_days = round(total) if has_plan else None
    elapsed_days = round(elapsed)
    progress = ScheduleProgress(
        segments=segments, has_plan=has_plan, pitched=pitched, completed=completed,
        elapsed_days=elapsed_days,
        expected_total_days=total_days,
        remaining_days=max(total_days - elapsed_days, 0) if has_plan else None,
        percent=percent, marker=marker, started_at=started_at,
        forecast_end=started_at + timedelta(days=total) if has_plan and pitched and not completed else None,
        completed_at=completed_at,
    )
    logger.debug(f"schedule_progress: {[(s.stage.name, s.stage.status, round(s.expected_days, 2)) for s in segments]} "
                 f"elapsed={elapsed:.2f} total={total:.2f} percent={percent} marker={marker}")
    return progress


def _step_label_rows(positions: list[float]) -> list[int]:
    """A label row (0/1) per step position: the first row with room, else the roomier one."""
    last: list = [None, None]
    rows = []
    for at in positions:
        gaps = [inf if previous is None else at - previous for previous in last]
        row = 0 if gaps[0] >= PROGRESS_STEP_GAP else 1 if gaps[1] >= PROGRESS_STEP_GAP else int(gaps[1] > gaps[0])
        last[row] = at
        rows.append(row)
    return rows


def _bar_at(segment: ProgressSegment, days_in: float) -> float:
    """Where `days_in` days into a stage falls on the bar (% of the bar), kept inside the segment."""
    if not segment.expected_days:
        return segment.left
    return segment.left + segment.width * min(max(days_in / segment.expected_days, 0.0), 1.0)


def _stays_and_steps(progress: ScheduleProgress, stays: list[VesselStay], plan_rows,
                     now: datetime) -> tuple[list[ProgressStay], list[ProgressStep]]:
    """
    Build stage 2 (TODO.txt PROGRESS BAR): each vessel stay inside its stage and
    each step as a marker where it happened; then, from the plan (plan_progress
    rows), the current stay's planned rest and the stays and steps still ahead.
    Time maps linearly inside each stage's segment.
    """
    segments = {segment.stage.name: segment for segment in progress.segments}
    stage_start: dict = {}
    for stay in stays:
        if stay.state in segments:
            stage_start.setdefault(stay.state, stay.start)

    def days_in(state: str, when: datetime) -> float:
        return (when - stage_start[state]).total_seconds() / 86400

    placed: list[ProgressStay] = []
    steps: list[ProgressStep] = []
    for stay in stays:
        if stay.state not in segments:
            continue                                  # a transfer before Pitch
        segment, end = segments[stay.state], stay.end or now
        left, right = _bar_at(segment, days_in(stay.state, stay.start)), _bar_at(segment, days_in(stay.state, end))
        placed.append(ProgressStay(
            kind='current' if stay.is_open else 'past',
            label=stay.vessel.name if stay.vessel else (stay.event.get_packaging_display() or "—"),
            vessel=stay.vessel, step=stay.label, left=left, width=right - left, start=stay.start, end=stay.end,
            days=round((end - stay.start).total_seconds() / 86400), notes=stay.event.notes,
        ))
        steps.append(ProgressStep(stay.label, 'done', left, when=stay.start, notes=stay.event.notes))
    if progress.completed:
        steps.append(ProgressStep("Complete Batch", 'done', 100.0, when=progress.completed_at))

    if progress.has_plan and not progress.completed:
        rows = plan_rows or []
        cursor: dict = {}                              # per stage: days in where the next planned step starts
        current = next((seg for seg in progress.segments if seg.stage.status == 'current'), None)
        if current is not None:
            name = current.stage.name
            cursor[name] = days_in(name, now)
            row = next((r for r in rows if r.status == 'current'), None)
            open_at = next((i for i, stay in enumerate(placed) if stay.kind == 'current'), None)
            if row and row.planned_days and row.event and row.stage.to_state == name:
                planned_end = days_in(name, row.event.timestamp) + row.planned_days
            else:
                # The latest step isn't a timed planned step (e.g. an extra Racking, a Transfer):
                # the stay runs on through the stage's planned time not taken by steps still ahead.
                planned_end = current.expected_days - sum(
                    r.planned_days or 0 for r in rows if r.status == 'upcoming' and r.stage.to_state == name)
            if open_at is not None and planned_end > cursor[name]:
                stay = placed[open_at]
                placed[open_at] = replace(stay, tail=_bar_at(current, planned_end) - stay.right)
                cursor[name] = planned_end
        for row in rows:
            if row.status != 'upcoming':
                continue
            state = row.stage.to_state
            if state == BatchStage.STATE_COMPLETED:
                steps.append(ProgressStep(row.stage.name, 'ahead', 100.0, notes=row.step.notes))
                continue
            segment = segments.get(state)
            if segment is None or segment.stage.status == 'done':
                continue
            start = cursor.get(state, 0.0)
            at = _bar_at(segment, start)
            steps.append(ProgressStep(row.stage.name, 'ahead', at, notes=row.step.notes))
            if row.planned_days:
                cursor[state] = start + row.planned_days
                placed.append(ProgressStay(
                    kind='planned', label=row.step.vessel_type_hint, vessel=None, step=row.stage.name,
                    left=at, width=_bar_at(segment, cursor[state]) - at, days=round(row.planned_days),
                    notes=row.step.notes,
                ))

    steps.sort(key=lambda step: step.at)
    steps = [replace(step, row=row) for step, row in zip(steps, _step_label_rows([step.at for step in steps]))]
    logger.debug(f"_stays_and_steps: stays={[(s.kind, s.label, round(s.left, 1), round(s.width, 1)) for s in placed]} "
                 f"steps={[(s.name, s.status, round(s.at, 1)) for s in steps]}")
    return placed, steps


def batch_schedule_progress(stays: list[VesselStay], plan_steps, *, completed_at: Optional[datetime] = None,
                            plan_rows=None, full_aging: Optional[Span] = None,
                            now: Optional[datetime] = None) -> ScheduleProgress:
    """
    schedule_progress() for a batch, from data the batch page already loads
    (Batch.vessel_durations(), the batch plan's steps, plan_progress() rows and
    Batch.full_aging()) - no queries of its own. The clock starts at Pitch (the
    first stay in a stage). Adds the vessel stays and steps (build stage 2).
    """
    now = now or timezone.now()
    started_at = next((stay.start for stay in stays if stay.state is not None), None)
    progress = schedule_progress(progress_stages(stays, plan_steps, full_aging=full_aging, now=now),
                                 started_at=started_at, completed_at=completed_at)
    placed, steps = _stays_and_steps(progress, stays, plan_rows, now)
    return replace(progress, stays=placed, steps=steps)
