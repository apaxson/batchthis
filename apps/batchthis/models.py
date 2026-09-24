"""
  Copyright 2021 Stones River Meadery (aaron@stonesrivermead.com)

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
"""
import pdb

from dataclasses import dataclass
from django.db import models
from datetime import datetime, timedelta
from django.dispatch import receiver
from django.db.models.signals import post_save, m2m_changed
from django.utils.text import slugify
from django.core.files.storage import FileSystemStorage
from django.utils import timezone
from pint import Quantity
from quantityfield.fields import QuantityField
from .fields import DescriptiveQuantityField
import logging

logger = logging.getLogger(__name__)


#TODO Refactor "fermenter" to generic "Vessel" and add "Vessel use" to be 'fermenter','aging','serving',etc.
#TODO add "Packaging" to identify how the batch was finished

use_options = (("WTR", "Water Agent"),
                   ("BTL", "Bottling"),
                   ("PRI", "Primary"),
                   ("SEC", "Secondary"),
                   ("TER", "Tertiary/Aging"),
                   ("BOI", "Boil"),
                   ("TIN", "Tincture"),
                   ("RAC", "Racking"))

fs = FileSystemStorage(location='media/recipes')


class PrecisionQuantityField(QuantityField):
    def __init__(self, base_units, *args, unit_choices, precision='.2f', **kwargs):
        super(PrecisionQuantityField,self).__init__(base_units, *args, unit_choices, **kwargs)
        self.ureg.default_format = precision

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs['precision'] = self.ureg.default_format
        del kwargs['verbose_name'] #FIXME - Getting duplicate value.  Forcing removal
        return name, path, args, kwargs


class BatchStage(models.Model):
    """
    One TRANSITION (arrow) of the fixed batch workflow graph - see
    TODO-BatchStage.txt, "CANONICAL WORKFLOW GRAPH". The rows are seeded by
    migration 0035_default_load2; code refers to them by `shortid`.
    """
    # The workflow's STATES (boxes). A blank from_state means Start.
    STATE_FERMENTATION = "Fermentation"
    STATE_AGING = "Aging"
    STATE_BOTTLING = "Bottling"
    STATE_COMPLETED = "Completed"
    STATE_CHOICES = [
        (STATE_FERMENTATION, STATE_FERMENTATION),
        (STATE_AGING, STATE_AGING),
        (STATE_BOTTLING, STATE_BOTTLING),
        (STATE_COMPLETED, STATE_COMPLETED),
    ]
    FROM_STATE_CHOICES = [("", "Start")] + STATE_CHOICES

    # The states a batch passes through, in order (Completed is the end, not a stay),
    # and the step that enters each - matches the 0035_default_load2 seed.
    TIMELINE_STATES = (STATE_FERMENTATION, STATE_AGING, STATE_BOTTLING)
    ENTRY_STAGE_NAMES = {
        STATE_FERMENTATION: "Pitch",
        STATE_AGING: "Racking",
        STATE_BOTTLING: "Sterile Filtering",
    }

    # Seeded shortids that code relies on (see Batch.full_aging()).
    RACKING = "racking"
    FILTERING_SHORTIDS = frozenset({"coarse-filtering", "fine-filtering", "sterile-filtering"})

    class Meta:
        ordering = ['sort_order']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.shortid:
            self.shortid = slugify(self.name)
        super().save(*args, **kwargs)

    name = models.CharField(max_length=20)
    shortid = models.SlugField(unique=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    from_state = models.CharField(max_length=15, choices=FROM_STATE_CHOICES, blank=True)
    to_state = models.CharField(max_length=15, choices=STATE_CHOICES)
    # Logging this transition moves the batch into another clean vessel (Batch.transfer()).
    transfers_batch = models.BooleanField(default=False)
    description = models.CharField(max_length=100, blank=True)

# Create your models here.
class Unit(models.Model):
    def __str__(self):
        return self.name
    TEMPERATURE = 0
    CONCENTRATION = 1
    WEIGHT = 2
    PH = 3
    TIME = 4
    VOLUME = 5
    CATEGORIES = (
        (TEMPERATURE, ("Temperature")),
        (CONCENTRATION, ("Concentration/Density")),
        (WEIGHT, ("Weight/Mass")),
        (PH, ("pH")),
        (TIME, ("Timing")),
        (VOLUME, ("Volume"))
    )
    identifier = models.CharField(max_length=10, help_text="Enter the unit identifier, i.e. 'mgL' or 'ph'")
    label = models.CharField(max_length=25, null=True, help_text="Enter abbreviation label of the measured unit, i.e. 'mg/L'")
    name = models.CharField(max_length=25, null=True, help_text="Descriptive Name of the measuring unit.")
    category = models.SmallIntegerField(choices = CATEGORIES, null=False)
# TODO: Add Unit Categories in Objects


# TODO: Add User Roles/Permissions
class Vessel(models.Model):
    STATUS_ACTIVE = 'In Use'
    STATUS_READY = "Clean/Ready"
    STATUS_DIRTY = "Needs Cleaning"
    # Pulled from use (repair, problem, retired). Set/cleared by hand only; see
    # services.take_vessel_out_of_service() / return_vessel_to_service().
    STATUS_OUT = "Out of Service"
    STATUS_CHOICES = (
        (STATUS_READY, STATUS_READY),
        (STATUS_ACTIVE, STATUS_ACTIVE),
        (STATUS_DIRTY, STATUS_DIRTY),
        (STATUS_OUT, STATUS_OUT),
    )

    def __str__(self):
        return f"{self.name} ({self.capacity})"
    name = models.CharField(max_length=25)
    # Stored in liters, returned in the unit entered (see DescriptiveQuantityField).
    capacity = DescriptiveQuantityField(base_units='liters', unit_choices=['liters', 'gallons'])
    fill = DescriptiveQuantityField(base_units='liters', unit_choices=['liters', 'gallons'], null=True, blank=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_READY)
    intended_use = models.CharField(max_length=30)

    @property
    def current_status_event(self):
        # Meta.ordering on VesselStatusEvent is oldest-first (a readable history
        # log), so the latest event needs its own explicit descending order.
        return self.status_events.order_by('-timestamp').first()

    def time_in_current_status(self):
        event = self.current_status_event
        if event is None:
            return None
        return timezone.now() - event.timestamp

    # Cellar Ledger badge modifier (cellar-ledger.css .cl-stage--*) per status.
    STATUS_BADGE_CLASSES = {
        STATUS_READY: 'cl-stage--ready',
        STATUS_ACTIVE: 'cl-stage--active',
        STATUS_DIRTY: 'cl-stage--dirty',
        STATUS_OUT: 'cl-stage--out',
    }

    @property
    def status_badge_class(self) -> str:
        return self.STATUS_BADGE_CLASSES.get(self.status, '')

    @property
    def vessel_type(self) -> str:
        # .all() rather than .exists() so prefetch_related() on list queries is used.
        if self.fermenter_set.all():
            return 'Fermenter'
        if self.agingtank_set.all():
            return 'Aging Tank'
        if self.barrel_set.all():
            return 'Barrel'
        return ''

    @property
    def current_batch(self):
        return Batch.objects.in_vessel(self).filter(active=True).first()


class VesselStatusEvent(models.Model):
    """
    Append-only, timestamped log of a Vessel's status transitions - same shape
    as BatchTest/BatchNote. See TODO-VesselLifecycle.txt. Only written by
    services.set_vessel_status(), which keeps Vessel.status (the current-value
    cache) and this log in sync.
    """
    class Meta:
        ordering = ['timestamp']
        verbose_name = 'vessel status event'

    def __str__(self):
        fmt = "%m/%d/%y-%H:%M"
        return f"{self.vessel} -> {self.status} ({self.timestamp.strftime(fmt)})"

    vessel = models.ForeignKey(Vessel, on_delete=models.CASCADE, related_name='status_events')
    status = models.CharField(max_length=15, choices=Vessel.STATUS_CHOICES)
    timestamp = models.DateTimeField(default=timezone.now)
    batch = models.ForeignKey('Batch', null=True, blank=True, on_delete=models.SET_NULL)
    notes = models.CharField(max_length=250, blank=True)
    # What the vessel was in before this event; lets Out of Service restore it.
    # Blank on events logged before this field existed.
    previous_status = models.CharField(max_length=15, choices=Vessel.STATUS_CHOICES, blank=True)

    @property
    def status_badge_class(self) -> str:
        return Vessel.STATUS_BADGE_CLASSES.get(self.status, '')


class InventoryItem(models.Model):
    class Meta:
        abstract = True

    name = models.CharField(max_length=75)
    supplier = models.CharField(max_length=75, null=True, blank=True)
    description = models.CharField(max_length=200, null=True, blank=True)
    version = models.IntegerField(null=True, blank=True)


class AdjunctType(models.Model):
    def __str__(self):
        return self.name

    name = models.CharField(max_length=30) # [Nutrient, Flavoring, Stablizing, Fining, Other]


class AdjunctUsage(models.Model):
    def __str__(self):
        return self.name
    name = models.CharField(max_length=25) # Bottling, Primary, Secondary, Aging, etc


class Adjunct(InventoryItem):
    type = models.ForeignKey(AdjunctType,on_delete=models.CASCADE)
    use = models.ForeignKey(AdjunctUsage, on_delete=models.CASCADE)
    use_for = models.CharField(max_length=50, null=True, blank=True)
    notes = models.CharField(max_length=200, null=True, blank=True)
    ratio_batch_size = models.CharField(max_length=15, null=True, blank=True)
    ratio_amount = models.CharField(max_length=15, null=True, blank=True)

    @property
    def display_name(self):
        if self.supplier:
            return self.supplier + ": " + self.name
        else:
            return self.name

    def __str__(self):
        return self.display_name


class FermentableType(models.Model):
    def __str__(self):
        return self.name

    name = models.CharField(max_length=10)


class Fermentable(InventoryItem):
    type = models.ForeignKey(FermentableType,on_delete=models.CASCADE)
    sugar_content = models.FloatField()  # Brix
    potential = models.FloatField() # Specific Gravity
    color = models.FloatField(blank=True,null=True) # SRM Number

    @property
    def display_name(self):
        if self.supplier:
            return self.supplier + ": " + self.name
        else:
            return self.name

    def __str__(self):
        return self.display_name


class Yeast(InventoryItem):
    type = models.CharField(max_length=20) # Ale, Champagne, Wine, Lager, etc
    form = models.CharField(max_length=20, choices=(('dry','Dry'),('liquid','Liquid')), verbose_name="Format")
    #min_temp = models.FloatField()
    min_temp = DescriptiveQuantityField(base_units='degC', unit_choices=['degC', 'degF'])
    #max_temp = models.FloatField()
    max_temp = DescriptiveQuantityField(base_units='degC', unit_choices=['degC', 'degF'])
    alc_tolerance = models.IntegerField(verbose_name="Alcohol Tolerance %", default=0)
    flocculation = models.CharField(max_length=7)
    attenuation = models.FloatField()
    notes = models.TextField(null=True, blank=True)

    @property
    def display_name(self):
        if self.supplier:
            return self.supplier + ": " + self.name
        else:
            return self.name

    @property
    def min_temp_f(self):
        return self.min_temp.to('degF')

    @property
    def max_temp_f(self):
        return self.max_temp.to('degF')

    def __str__(self):
        return self.display_name


class Fermenter(models.Model):
    def __str__(self):
        return self.vessel.name
    last_passivation = models.DateField(null=True)
    vessel = models.ForeignKey(Vessel, on_delete=models.CASCADE)


class AgingTank(models.Model):
    vessel = models.ForeignKey(Vessel, on_delete=models.CASCADE)


class Barrel(models.Model):
    serial = models.CharField(max_length=20) #Barcode / rfid / etc
    toastLevel = models.CharField(max_length=25)
    vessel = models.ForeignKey(Vessel, on_delete=models.CASCADE)


class BatchNoteType(models.Model):
    def __str__(self):
        return self.name
    name = models.CharField(max_length=50)


class BatchTestType(models.Model):
    def __str__(self):
        return self.name

    def save(self,*args,**kwargs):
        if not self.shortid:
            self.shortid = slugify(self.name)
        super(BatchTestType,self).save(*args,**kwargs)

    name = models.CharField(max_length = 25)
    shortid = models.SlugField(unique=True)


class BatchStyle(models.Model):
    def __str__(self):
        return self.name
    name = models.CharField(max_length=30)


class BatchCategory(models.Model):
    def __str__(self):
        if self.bjcp_code:
            return self.name + " (" + self.bjcp_code + ")"
        else:
            return self.name

    class Meta:
        verbose_name_plural = "batch categories"

    name = models.CharField(max_length=30)
    style = models.ForeignKey(BatchStyle, on_delete=models.CASCADE)
    bjcp_code = models.CharField(max_length=3)


class Recipe(models.Model):
    def __str__(self):
        return self.name

    class Meta:
        unique_together = ('name', 'version')

    bs_file = models.FileField(storage=fs,null=True)
    name = models.CharField(max_length=75)
    dateCreated = models.DateField()
    dateUpdated = models.DateField(null=True, blank=True)
    version = models.IntegerField(null=True, blank=True)
    category = models.ForeignKey(BatchCategory,on_delete=models.CASCADE)
    brewer = models.CharField(max_length=30, null=True)
    batchSize = DescriptiveQuantityField(base_units='liters', unit_choices=['liters','gallons'])
    source = models.CharField(max_length=50, null=True) #Where did the recipe come from
    pairing = models.CharField(max_length=250, null=True) # Textfield listing various foods.  TODO: Refactor
    notes = models.TextField()
    estOG = PrecisionQuantityField(base_units='sg', unit_choices=['sg'])
    estFG = DescriptiveQuantityField(base_units='sg', unit_choices=['sg'])
    estABV = models.FloatField()


class RecipeItem(models.Model):

    class Meta:
        abstract = True # We don't want our own table.  Inherit these fiels

    intended_use = models.ForeignKey(AdjunctUsage, on_delete=models.RESTRICT)
    _amount_weight = DescriptiveQuantityField('kilograms', null=True, blank=True, unit_choices=['lb', 'gram', 'oz', 'milligram', 'kilogram'])
    _amount_volume = DescriptiveQuantityField('liters', null=True, blank=True, unit_choices=['floz', 'ml', 'gallon', 'liter'])
    recipe_notes = models.CharField(max_length=200, null=True, blank=True)
    amount = DescriptiveQuantityField(null=True, blank=True)


class RecipeFermentable(RecipeItem):

    is_fermentable = models.BooleanField(null=True, blank=True)
    fermentable = models.ForeignKey(Fermentable, on_delete=models.RESTRICT)
    recipe = models.ManyToManyField(Recipe, related_name='fermentables')

    @property
    def display_name(self):
        if self.fermentable.supplier:
            return self.fermentable.supplier + ": " + self.fermentable.name
        else:
            return self.fermentable.name

    def __str__(self):
        return self.display_name


class RecipeYeasts(models.Model):
    yeast = models.ForeignKey(Yeast, on_delete=models.RESTRICT)
    amount = DescriptiveQuantityField(base_units='kilograms', unit_choices=['kilograms'])
    recipe = models.ManyToManyField(Recipe, related_name='yeasts')
    notes = models.CharField(max_length=200, null=True, blank=True)

    @property
    def display_name(self):
        return self.yeast.display_name

    def __str__(self):
        return self.display_name


class RecipeAdjunct(RecipeItem):
    adjunct = models.ForeignKey(Adjunct, on_delete=models.RESTRICT)
    # Minutes since start of Batch on when to add
    time_to_add = DescriptiveQuantityField(base_units='min', unit_choices=['min'])
    recipe = models.ManyToManyField(Recipe, related_name='adjuncts')

    def __str__(self):
        return self.adjunct.display_name


class ActivityLog(models.Model):
    datetime = models.DateTimeField()
    text = models.TextField()


class BatchQuerySet(models.QuerySet):
    def in_vessel(self, vessel: Vessel) -> "BatchQuerySet":
        # Batches created before Batch.vessel existed have it unset; they're
        # still in their starting fermenter (see Batch.current_vessel).
        return self.filter(
            models.Q(vessel=vessel) | models.Q(vessel__isnull=True, fermenter__vessel=vessel)
        )


class Batch(models.Model):
    objects = BatchQuerySet.as_manager()

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'batches'

    name = models.CharField(max_length=50)
    # Set once when the batch is created and never touched by later saves. Not
    # auto_now_add: that discards the start date entered on the Add Batch form.
    # Batch creation is not Pitch - pitching is logged as its own stage event.
    startdate = models.DateTimeField(default=timezone.now)
    enddate = models.DateTimeField(null=True, blank=True)
    lotId = models.CharField(max_length=7, null=True) # Bottledate L[2digityear][0paddedYearDays] = L22088
    size = DescriptiveQuantityField(base_units='liters', unit_choices=['liters','gallons'])
    active = models.BooleanField(default=True)
    fermenter = models.ForeignKey(Fermenter, on_delete=models.RESTRICT)
    # Where the batch is now; changed by transfer(). `fermenter` stays the one it started in.
    vessel = models.ForeignKey(Vessel, on_delete=models.RESTRICT, null=True, blank=True, related_name='batches')
    startingGravity = QuantityField(base_units="sg")
    estimatedEndGravity = QuantityField(base_units="sg")
    category = models.ForeignKey(BatchCategory, on_delete=models.RESTRICT, blank=True, null=True)
    activity = models.ManyToManyField(ActivityLog, blank=True, related_name='batch')
    recipe = models.ForeignKey(Recipe, on_delete=models.RESTRICT, null=True, blank=True)
    # TODO Add additional objects
    aging_vessel = None
    packaging = None
    # TODO Add pre_save signal to compare the two objects for fields changed.

    @property
    def current_vessel(self) -> Vessel:
        return self.vessel if self.vessel_id else self.fermenter.vessel

    def transfer(
        self,
        src_vessel: Vessel,
        dst_vessel: Vessel,
        *,
        timestamp: datetime | None = None,
        log_activity: bool = True,
        reason: str = "",
    ) -> None:
        """
        Move the batch from src_vessel into dst_vessel (any vessel type): src
        -> Needs Cleaning, dst -> In Use, batch.vessel -> dst, plus one
        ActivityLog entry on the batch. All of it commits together, or none.
        timestamp (default now) stamps the vessel status history. The stage
        workflow passes log_activity=False: its own entry covers the move. An
        optional reason (ad-hoc transfers) is added to the notes and the entry.
        """
        from django.core.exceptions import ValidationError
        from django.db import transaction
        from .services import add_activity_log, set_vessel_status

        timestamp = timestamp or timezone.now()

        logger.debug(
            f"Batch.transfer: batch={self.pk} '{self.name}' "
            f"src={src_vessel.pk} '{src_vessel.name}' dst={dst_vessel.pk} '{dst_vessel.name}'"
        )
        current = self.current_vessel
        problem = None
        if not self.active:
            problem = f"Batch '{self.name}' is complete and can't be transferred."
        elif src_vessel.pk != current.pk:
            problem = f"Batch '{self.name}' is in '{current.name}', not '{src_vessel.name}'."
        elif dst_vessel.pk == src_vessel.pk:
            problem = f"Batch '{self.name}' is already in '{dst_vessel.name}'."
        elif dst_vessel.status != Vessel.STATUS_READY:
            problem = f"'{dst_vessel.name}' is {dst_vessel.status}; transfers need a {Vessel.STATUS_READY} vessel."
        if problem:
            logger.error(f"Batch.transfer: rejected - {problem}")
            raise ValidationError(problem)

        with transaction.atomic():
            self.vessel = dst_vessel
            self.save(update_fields=['vessel'])
            why = f" - {reason}" if reason else ""
            set_vessel_status(src_vessel, Vessel.STATUS_DIRTY, batch=self,
                              notes=f"Batch transferred to {dst_vessel.name}{why}"[:250], timestamp=timestamp)
            set_vessel_status(dst_vessel, Vessel.STATUS_ACTIVE, batch=self,
                              notes=f"Batch transferred from {src_vessel.name}{why}"[:250], timestamp=timestamp)
            if log_activity:
                text = f"Transferred from [{src_vessel.name}] to [{dst_vessel.name}]" + (f" :: {reason}" if reason else "")
                add_activity_log(self, text, timestamp=timestamp)
        logger.info(f"Batch '{self.name}' transferred from '{src_vessel.name}' to '{dst_vessel.name}'")

    def complete(self, *, timestamp: datetime | None = None) -> None:
        """
        Mark the batch finished and flag its current vessel for cleaning. Both
        commit together, or neither does. timestamp (default now) becomes the
        end date and stamps the vessel status history.
        """
        # Local import: services.py imports this module.
        from django.core.exceptions import ValidationError
        from django.db import transaction
        from .services import set_vessel_status

        logger.debug(f"Batch.complete: batch={self.pk} '{self.name}' active={self.active}")
        if not self.active:
            logger.error(f"Batch.complete: batch {self.pk} '{self.name}' is already complete")
            raise ValidationError(f"Batch '{self.name}' is already complete.")

        vessel = self.current_vessel
        timestamp = timestamp or timezone.now()
        with transaction.atomic():
            self.enddate = timestamp
            self.active = False
            self.save()
            set_vessel_status(vessel, Vessel.STATUS_DIRTY, batch=self, notes="Batch completed", timestamp=timestamp)
        logger.info(f"Batch '{self.name}' completed; vessel '{vessel.name}' needs cleaning")

    @property
    def current_stage_event(self) -> "BatchStageEvent | None":
        """The newest workflow stage event (transfer-only events don't change the stage)."""
        return (
            self.stage_events.filter(stage__isnull=False)
            .select_related('stage', 'vessel').order_by('-timestamp', '-pk').first()
        )

    @property
    def current_state(self) -> str | None:
        """The workflow state the batch is in (BatchStage.STATE_*), or None before Pitch."""
        event = self.current_stage_event
        return event.stage.to_state if event else None

    def _timeline_events(self) -> list["BatchStageEvent"]:
        # Reuse prefetch_related('stage_events') when the caller has done it, so a
        # page showing several durations for a batch doesn't query once per duration.
        if 'stage_events' in getattr(self, '_prefetched_objects_cache', {}):
            return list(self.stage_events.all())
        return list(self.stage_events.select_related('stage', 'vessel'))

    def vessel_durations(self) -> list["VesselStay"]:
        """
        "Time in vessel": one stay per stage event, running until the next event -
        so every transfer (Racking, any Filtering) starts a new stay. The newest
        stay stays open and is measured up to now. Complete Batch ends the
        timeline rather than starting a stay of its own.
        """
        events = self._timeline_events()
        now = timezone.now()
        stays = []
        state = None
        for i, event in enumerate(events):
            if event.stage is not None:
                state = event.stage.to_state
            if state == BatchStage.STATE_COMPLETED:
                continue
            end = events[i + 1].timestamp if i + 1 < len(events) else None
            stays.append(VesselStay(
                event=event,
                # A transfer-only event keeps the state the batch was already in.
                state=state,
                vessel=event.vessel,
                start=event.timestamp,
                end=end,
                duration=(end or now) - event.timestamp,
            ))
        logger.debug(f"vessel_durations: batch={self.pk} {len(events)} events -> {len(stays)} stays")
        return stays

    def full_aging(self) -> "Span | None":
        """
        "Full aging": from the first Racking until the first filtering of any kind
        (Coarse, Fine or Sterile). Later rackings don't restart it, and nothing
        after that first filtering extends it. Open (measured up to now) until a
        filtering is logged; None before the first Racking.
        """
        events = self._timeline_events()
        start = next((e.timestamp for e in events if e.stage and e.stage.shortid == BatchStage.RACKING), None)
        if start is None:
            return None
        end = next(
            (e.timestamp for e in events
             if e.stage and e.stage.shortid in BatchStage.FILTERING_SHORTIDS and e.timestamp >= start),
            None,
        )
        span = Span(start=start, end=end, duration=(end or timezone.now()) - start)
        logger.debug(f"full_aging: batch={self.pk} {span}")
        return span

    def timeline_bar(self, planned_durations: dict[str, timedelta] | None = None) -> "TimelineBar":
        """
        The batch page's time bar: past and current vessel stays (widths = real
        time), then the states still ahead as future segments. A future state's
        width comes from planned_durations[state] when given (phase 2: the recipe
        or pre-batch plan sets time per stage), else a fixed placeholder width.
        """
        planned_durations = planned_durations or {}
        stays = self.vessel_durations()
        events = self._timeline_events()
        completed = next((e for e in reversed(events) if e.stage and e.stage.to_state == BatchStage.STATE_COMPLETED), None)

        segments = [
            TimelineSegment(
                kind='current' if stay.is_open else 'past',
                state=stay.state, label=stay.label, vessel=stay.vessel, notes=stay.event.notes,
                start=stay.start, end=stay.end, duration=stay.duration,
                weight=max(stay.duration.total_seconds(), 60.0),
            )
            for stay in stays
        ]

        if completed is None:
            reached = {s.state for s in segments}
            current = segments[-1].state if segments else None
            ahead = [st for st in BatchStage.TIMELINE_STATES if st not in reached and st != current]
            known = sum(s.weight for s in segments)
            placeholder = max(known, 86400.0) * self.TIMELINE_PLACEHOLDER_SHARE
            for state in ahead:
                planned = planned_durations.get(state)
                segments.append(TimelineSegment(
                    kind='future', state=state, label=BatchStage.ENTRY_STAGE_NAMES[state], vessel=None, notes="",
                    start=None, end=None, duration=planned, planned=planned is not None,
                    weight=planned.total_seconds() if planned else placeholder,
                ))

        bands: list[TimelineBand] = []
        for segment in segments:
            if bands and bands[-1].state == segment.state:
                bands[-1].segments.append(segment)
            else:
                bands.append(TimelineBand(state=segment.state, segments=[segment]))

        logger.debug(f"timeline_bar: batch={self.pk} {len(segments)} segments in {len(bands)} bands")
        return TimelineBar(segments=segments, bands=bands, completed_at=completed.timestamp if completed else None)

    # A future stage with no planned duration is drawn this share of the known time.
    TIMELINE_PLACEHOLDER_SHARE = 0.15

    def current_gravity(self):
        gravity_tests = self.tests.filter(type__shortid='specific-gravity')
        if len(gravity_tests) > 1:
            return gravity_tests.last().chart_value
        return gravity_tests[0].chart_value


    def percent_complete(self):
        est_fg = self.estimatedEndGravity.magnitude
        current_gravity = self.current_gravity()
        return round((self.startingGravity.magnitude - current_gravity) / (self.startingGravity.magnitude - est_fg) * 100)


class BatchStageEvent(models.Model):
    """
    One workflow transition (BatchStage) logged against a batch - append-only,
    like BatchTest/BatchNote. Written by the stage-logging service (step 4 in
    TODO-BatchStage.txt), which also transfers/completes the batch and writes
    the batch's ActivityLog; don't create these directly elsewhere.
    """
    class Meta:
        # pk breaks ties between events logged with the same timestamp.
        ordering = ['timestamp', 'pk']

    def __str__(self):
        fmt = "%m/%d/%y-%H:%M"
        return f"{self.batch} - {self.label} ({self.timestamp.strftime(fmt)})"

    @property
    def label(self) -> str:
        return self.stage.name if self.stage else "Transfer"

    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name='stage_events')
    # Blank = an ad-hoc transfer outside the workflow (services.transfer_batch()):
    # the batch changed vessel but not stage.
    stage = models.ForeignKey(BatchStage, on_delete=models.PROTECT, related_name='events', null=True, blank=True)
    # Editable and defaulted, not auto_now_add - stages are often logged after the fact.
    timestamp = models.DateTimeField(default=timezone.now)
    # The vessel the batch is in AFTER this event: the transfer destination for
    # Racking/Filtering, the batch's current vessel otherwise.
    vessel = models.ForeignKey(Vessel, null=True, blank=True, on_delete=models.SET_NULL, related_name='stage_events')
    notes = models.CharField(max_length=250, blank=True)


@dataclass(kw_only=True)
class Span:
    """A measured stretch of a batch's timeline, e.g. Batch.full_aging()."""
    start: datetime
    end: datetime | None  # None while still open; duration is then measured up to now
    duration: timedelta

    @property
    def is_open(self) -> bool:
        return self.end is None


@dataclass(kw_only=True)
class VesselStay(Span):
    """One entry of Batch.vessel_durations(): time in `vessel`, in `state`, after `event`."""
    event: BatchStageEvent
    state: str | None  # None only for a transfer before Pitch
    vessel: Vessel | None

    @property
    def label(self) -> str:
        return self.event.label


@dataclass(kw_only=True)
class TimelineSegment:
    """One block of Batch.timeline_bar(): a vessel stay (past/current) or a state still ahead (future)."""
    kind: str  # 'past' | 'current' | 'future'
    state: str | None
    label: str  # the step that started it (Pitch, Racking, Transfer, ...)
    vessel: Vessel | None
    notes: str
    start: datetime | None
    end: datetime | None
    duration: timedelta | None  # actual time; planned time for a planned future stage; else None
    weight: float  # relative width
    planned: bool = False


@dataclass(kw_only=True)
class TimelineBand:
    """Consecutive timeline segments in the same state (Fermentation, Aging, Bottling)."""
    state: str | None
    segments: list[TimelineSegment]

    @property
    def weight(self) -> float:
        return sum(s.weight for s in self.segments)

    @property
    def kind(self) -> str:
        kinds = {s.kind for s in self.segments}
        return 'current' if 'current' in kinds else 'future' if kinds == {'future'} else 'past'

    @property
    def start(self) -> datetime | None:
        return self.segments[0].start

    @property
    def end(self) -> datetime | None:
        return self.segments[-1].end

    @property
    def duration(self) -> timedelta | None:
        durations = [s.duration for s in self.segments]
        return None if any(d is None for d in durations) else sum(durations, timedelta())


@dataclass(kw_only=True)
class TimelineBar:
    segments: list[TimelineSegment]
    bands: list[TimelineBand]
    completed_at: datetime | None

    @property
    def now_index(self) -> int | None:
        """Index of the segment right after the current one - where "Now" falls - if any."""
        for i, segment in enumerate(self.segments):
            if segment.kind == 'current':
                return i + 1
        return None


@dataclass(frozen=True)
class ReadingSpec:
    """
    The units a test type's readings take (Aaron's table) and how a reading is
    charted and shown. `kind` picks the rules below; `units_label` and `example`
    feed the form's error message and placeholder.
    """
    kind: str  # gravity | temperature | concentration | acidity | ph
    units_label: str
    example: str
    positive: bool = True

    def problem(self, type_name: str, quantity) -> tuple[str, str] | None:
        """(error code, message) if `quantity` isn't a valid reading of this type, else None."""
        unit = str(quantity.units)
        bare = unit == 'dimensionless'
        wrong = ('wrong_unit', f"Use {self.units_label} for {type_name}, e.g. {self.example}.")
        if self.kind == 'ph':
            if not bare:
                return 'wrong_unit', f"pH has no unit, e.g. {self.example}."
        elif self.kind == 'gravity':
            # sg, a bare number, or Brix (converted in normalize(), never by pint).
            if not (bare or unit in ('SpecificGravity', 'Brix')):
                return wrong
        elif bare:
            return 'units_required', "Units are required."
        elif self.kind == 'temperature' and not quantity.check('[temperature]'):
            return wrong
        elif self.kind == 'concentration' and not (unit == 'ppm' or quantity.check('[mass] / [volume]')):
            return wrong
        elif self.kind == 'acidity' and not quantity.check('[mass] / [volume]'):
            return wrong
        if self.positive and quantity.magnitude <= 0:
            return 'not_positive', "Enter an amount greater than zero."
        return None

    # A bare Specific Gravity reading above this is a refractometer Brix reading (Aaron).
    BRIX_THRESHOLD = 1.199

    def normalize(self, quantity, *, start_sg: float | None = None):
        """
        (value to store, note or None). Gravity is always stored as sg: a bare number
        up to 1.199 is sg; above that, or with a Brix unit, it's Brix and is converted
        like the refractometer tool - alcohol-corrected against the batch's starting
        gravity (plain Brix -> SG if there isn't one). pint can't do this conversion:
        the registry defines sg and Brix 1:1, and the real relationship isn't linear.
        """
        if self.kind != 'gravity':
            return quantity, None
        from .lib.utils import Utils  # local: keep lib imports out of model load

        unit, magnitude = str(quantity.units), quantity.magnitude
        sg = quantity._REGISTRY.sg
        if unit == 'SpecificGravity' or (unit == 'dimensionless' and magnitude <= self.BRIX_THRESHOLD):
            return magnitude * sg, None
        if start_sg:
            gravity, _abv = Utils.refractometerCorrection(startSG=start_sg, currentBrix=magnitude)
            note = f"from {magnitude:g} °Bx (refractometer, alcohol-corrected)"
        else:
            gravity = Utils.brixToSg(magnitude)
            note = f"from {magnitude:g} °Bx (refractometer)"
        logger.debug(f"ReadingSpec.normalize: {magnitude:g} Brix -> {gravity:.4f} sg (start_sg={start_sg})")
        return gravity * sg, note

    def chart_value(self, quantity) -> float:
        """One number per type for charts and fault rules: sg, °F, ppm (1 mg/L = 1 ppm), g/L, pH."""
        unit = str(quantity.units)
        if self.kind == 'temperature':
            return quantity.to('degF').magnitude
        if self.kind == 'concentration':
            return quantity.to('ppm').magnitude if unit == 'ppm' else quantity.to('mg/L').magnitude
        if self.kind == 'acidity':
            return quantity.to('g/L').magnitude
        if self.kind == 'gravity' and unit != 'dimensionless':
            return quantity.to('sg').magnitude
        return quantity.magnitude

    def display(self, quantity) -> str:
        unit = str(quantity.units)
        if self.kind == 'gravity':
            return f"{self.chart_value(quantity):.3f}"  # CLAUDE.md: SG always 3 decimals
        if self.kind == 'ph':
            return f"{quantity.magnitude:.2f}"
        if self.kind == 'temperature':
            return f"{quantity.magnitude:g} {'°F' if 'Fahrenheit' in unit else '°C'}"
        if self.kind == 'concentration':
            return f"{quantity.magnitude:g} ppm" if unit == 'ppm' else f"{quantity.to('mg/L').magnitude:g} mg/L"
        return f"{quantity.to('g/L').magnitude:g} g/L"


# Keyed by BatchTestType.shortid (seeded in 0002_default_load). A test type not
# listed here accepts any reading and charts its raw number.
READING_SPECS = {
    'specific-gravity': ReadingSpec('gravity', 'sg', '1.050 sg'),
    'temperature': ReadingSpec('temperature', '°F or °C', '68 °F', positive=False),
    'so2': ReadingSpec('concentration', 'ppm or mg/L', '30 ppm'),
    'yan': ReadingSpec('concentration', 'ppm or mg/L', '250 ppm'),
    'ta': ReadingSpec('acidity', 'g/L', '6.5 g/L'),
    'ph': ReadingSpec('ph', '', '3.40'),
}


class BatchTest(models.Model):
    def __str__(self):
        fmt = "%m/%d/%y-%H:%M"
        return self.datetime.strftime(fmt) + " " + self.type.name

    datetime = models.DateTimeField(auto_now=False)
    type = models.ForeignKey(BatchTestType, on_delete=models.SET("_del"))
    # The reading with the unit it was entered in ("1.050 sg", "68 °F", "30 ppm", "3.40"),
    # stored in metric/base units - see DescriptiveQuantityField and READING_SPECS.
    value = DescriptiveQuantityField(base_units='dimensionless')
    description = models.CharField(max_length=250, blank=True)
    batch = models.ForeignKey(Batch, blank=True, on_delete=models.CASCADE, related_name="tests")

    @property
    def spec(self) -> ReadingSpec | None:
        return READING_SPECS.get(self.type.shortid)

    def _quantity(self):
        # A just-created reading may still hold the text it was created with.
        return self._meta.get_field('value').to_python(self.value)

    @property
    def chart_value(self) -> float:
        """The reading as one number in its type's standard unit - for charts and fault rules."""
        quantity = self._quantity()
        return self.spec.chart_value(quantity) if self.spec else quantity.magnitude

    @property
    def display_value(self) -> str:
        quantity = self._quantity()
        return self.spec.display(quantity) if self.spec else str(quantity)

#TODO Add Notifications for Users on tasks and updates: https://stackoverflow.com/questions/72264677/how-can-i-implement-notifications-system-in-django

# If a batch is saved with a Starting Gravity, add that test
@receiver(post_save,sender=Batch)
def addGravityTest(sender,instance,created=False,**kwargs):
    # Only need to do it if Batch is created.
    if created:
        if instance.startingGravity:
            gravTest = BatchTest()
            testType = BatchTestType.objects.filter(shortid='specific-gravity')[0]
            gravTest.type = testType
            gravTest.value = f"{instance.startingGravity.magnitude} sg"
            gravTest.description = "Auto created from new batch."
            gravTest.datetime = datetime.now()
            gravTest.batch = instance
            gravTest.save()
            logger.debug(f"Added Gravity Test: {gravTest.value} automatically to newly created batch_id: {instance.id}")


# TODO: Refactor to match Adjuncts/RecipeAdjuncts
class BatchAdditionItem(models.Model):
    # Sulfites, Acids, Hops, Nutrients, Fruit, etc
    # Creating a model, rather than typing, so reports can be made on which batches used a specific item
    def __str__(self):
        if self.lotid:
            return self.name + " (" + self.lotid + ")"
        else:
            return self.name
    name = models.CharField(max_length=50, help_text="Name of this addition item.")
    maker = models.CharField(max_length=50, blank=True, help_text="Name of the company who made this item.")
    lotid = models.CharField(max_length=20, blank=True, help_text="The lot or batch id of this item.  Useful when looking for batches made with bad Lot")


# TODO: Refactor to match Adjuncts/RecipeAdjuncts
class BatchAddition(models.Model):
    # PROTECT: an adjunct that's been added to a batch can't be deleted, so batch history stays intact.
    adjunct = models.ForeignKey(Adjunct, on_delete=models.PROTECT, related_name='batch_additions')
    description = models.CharField(max_length=250, blank=True, help_text="Add a brief description of this Addition item and why")
    # A weight or a volume, stored in metric and returned as entered (see DescriptiveQuantityField).
    amount = DescriptiveQuantityField(base_units='kilograms')
    batch = models.ForeignKey(Batch, blank=True, on_delete=models.CASCADE, related_name="additions")


class BatchNote(models.Model):
    def __str__(self):
        fmt = "%m/%d/%y-%H:%M"
        return self.date.strftime(fmt) + " " + self.text[:50]

    text = models.TextField()
    date = models.DateTimeField(auto_now_add=False)
    notetype = models.ForeignKey(BatchNoteType,on_delete=models.SET("_del"))
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE,related_name="notes")

# TODO: Add Recipe to activity log
# TODO: Add Adjuncts/Fermentables/Yeasts to activity log
# TODO: Add username to activity logs


@receiver(post_save,sender=BatchNote)
@receiver(post_save,sender=BatchAddition)
@receiver(post_save,sender=BatchTest)
@receiver(post_save,sender=Batch)
def addActivity(sender,instance,created=False,**kwargs):
    text = None
    batch = None
    date = datetime.now()
    if sender.__name__ == "Batch":
        batch = instance
        if created:
            text = "Batch Created"
        else:
            text = "Batch Modified"
    if sender.__name__ == "BatchNote":
        batch = instance.batch
        if created:
            text = "Added ["+instance.notetype.name+"] :: " + instance.text
    if sender.__name__ == "BatchAddition":
        batch = instance.batch
        if created:
            # amount may still be the text it was created with ("4 grams"); show it as stored.
            amount = instance._meta.get_field('amount').to_python(instance.amount)
            text = f"Added [{instance.adjunct.display_name}] :: {amount}"
        else:
            text = f"Updated [{instance.adjunct.display_name}]"
    if sender.__name__ == "BatchTest":
        batch = instance.batch
        if created:
            text = f"Added [{instance.type.name}] :: {instance.display_value}"
        else:
            text = "Updated [" + instance.type.name + "]"
    if text:
        log = ActivityLog(datetime=date,text=text)
        log.save()
        batch.activity.add(log)

