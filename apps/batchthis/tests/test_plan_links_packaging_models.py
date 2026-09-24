"""Step 11a: stage events link to their planned step; batches can be packaged (Bottles / Kegs)."""
import pytest
from django.db import models

from ..factories import BatchFactory, FermenterFactory, VesselFactory
from ..models import Batch, BatchPlanStep, BatchStage, BatchStageEvent, PlanStep, Vessel

BOTTLES, KEGS = PlanStep.VESSEL_BOTTLES, PlanStep.VESSEL_KEGS


# ---------- BatchStageEvent.plan_step ----------

def test_a_stage_event_can_link_to_the_planned_step_it_fulfils():
    field = BatchStageEvent._meta.get_field("plan_step")

    assert field.related_model is BatchPlanStep
    assert field.null and field.blank
    assert field.remote_field.on_delete is models.SET_NULL


@pytest.mark.django_db
def test_deleting_a_planned_step_keeps_the_event_as_unplanned():
    batch = BatchFactory()
    step = BatchPlanStep.objects.create(batch=batch, sort_order=1, stage=BatchStage.objects.get(shortid="pitch"),
                                        planned_duration="14 days", vessel_type=Vessel.TYPE_FERMENTER)
    event = BatchStageEvent.objects.create(batch=batch, stage=step.stage, vessel=batch.fermenter.vessel,
                                           plan_step=step)

    step.delete()

    event.refresh_from_db()
    assert event.plan_step is None


# ---------- packaging fields ----------

@pytest.mark.parametrize("model", [Batch, BatchStageEvent])
def test_packaging_is_optional_bottles_or_kegs(model):
    field = model._meta.get_field("packaging")

    assert field.choices == PlanStep.PACKAGING_CHOICES
    assert field.blank and field.default == ""


@pytest.mark.django_db
def test_new_batches_and_events_are_not_packaged():
    batch = BatchFactory()

    assert batch.packaging == "" and not batch.is_packaged


# ---------- Batch.current_vessel ----------

@pytest.mark.django_db
def test_current_vessel_is_the_vessel_the_batch_is_in():
    tank = VesselFactory(name="Tank A")
    batch = BatchFactory(vessel=tank)

    assert batch.current_vessel == tank


@pytest.mark.django_db
def test_current_vessel_falls_back_to_the_starting_fermenter_for_older_batches():
    fermenter = FermenterFactory()
    batch = BatchFactory(fermenter=fermenter, vessel=None)

    assert batch.current_vessel == fermenter.vessel


@pytest.mark.django_db
@pytest.mark.parametrize("packaging", [BOTTLES, KEGS])
def test_a_packaged_batch_has_no_current_vessel(packaging):
    batch = BatchFactory(vessel=None, packaging=packaging)

    assert batch.is_packaged
    assert batch.current_vessel is None
    assert batch.get_packaging_display() == packaging
