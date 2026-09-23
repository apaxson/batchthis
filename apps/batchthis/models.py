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
        return self.name + " (" + str(self.max_size) + self.max_size_units.identifier + ")"
    name = models.CharField(max_length=25)
    max_size = models.IntegerField()
    max_size_units = models.ForeignKey(Unit, related_name="fermenter_max_size_units", on_delete=models.SET("_del"))
    used_size = models.IntegerField(blank=True, null=True)
    used_size_units = models.ForeignKey(Unit, blank=True, null=True,related_name="fermenter_used_size_units", on_delete=models.SET("_del"))
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

    def transfer(self, src_vessel: Vessel, dst_vessel: Vessel) -> None:
        """
        Move the batch from src_vessel into dst_vessel (any vessel type): src
        -> Needs Cleaning, dst -> In Use, batch.vessel -> dst, plus one
        ActivityLog entry on the batch. All of it commits together, or none.
        """
        from django.core.exceptions import ValidationError
        from django.db import transaction
        from .services import set_vessel_status

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
            set_vessel_status(src_vessel, Vessel.STATUS_DIRTY, batch=self, notes=f"Batch transferred to {dst_vessel.name}")
            set_vessel_status(dst_vessel, Vessel.STATUS_ACTIVE, batch=self, notes=f"Batch transferred from {src_vessel.name}")
            log = ActivityLog.objects.create(
                datetime=timezone.now(), text=f"Transferred from [{src_vessel.name}] to [{dst_vessel.name}]"
            )
            self.activity.add(log)
        logger.info(f"Batch '{self.name}' transferred from '{src_vessel.name}' to '{dst_vessel.name}'")

    def complete(self) -> None:
        """
        Mark the batch finished and flag its fermentation vessel for cleaning.
        Both commit together, or neither does.
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
        with transaction.atomic():
            self.enddate = timezone.now()
            self.active = False
            self.save()
            set_vessel_status(vessel, Vessel.STATUS_DIRTY, batch=self, notes="Batch completed")
        logger.info(f"Batch '{self.name}' completed; vessel '{vessel.name}' needs cleaning")

    @property
    def current_stage_event(self) -> "BatchStageEvent | None":
        return self.stage_events.select_related('stage', 'vessel').order_by('-timestamp', '-pk').first()

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
        for i, event in enumerate(events):
            if event.stage.to_state == BatchStage.STATE_COMPLETED:
                continue
            end = events[i + 1].timestamp if i + 1 < len(events) else None
            stays.append(VesselStay(
                event=event,
                state=event.stage.to_state,
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
        start = next((e.timestamp for e in events if e.stage.shortid == BatchStage.RACKING), None)
        if start is None:
            return None
        end = next(
            (e.timestamp for e in events
             if e.stage.shortid in BatchStage.FILTERING_SHORTIDS and e.timestamp >= start),
            None,
        )
        span = Span(start=start, end=end, duration=(end or timezone.now()) - start)
        logger.debug(f"full_aging: batch={self.pk} {span}")
        return span

    def current_gravity(self):
        gravity_tests = self.tests.filter(type__shortid='specific-gravity')
        if len(gravity_tests) > 1:
            return gravity_tests.last().value
        return gravity_tests[0].value


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
        return f"{self.batch} - {self.stage} ({self.timestamp.strftime(fmt)})"

    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name='stage_events')
    stage = models.ForeignKey(BatchStage, on_delete=models.PROTECT, related_name='events')
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
    state: str
    vessel: Vessel | None


class BatchTest(models.Model):
    def __str__(self):
        fmt = "%m/%d/%y-%H:%M"
        return self.datetime.strftime(fmt) + " " + self.type.name

    datetime = models.DateTimeField(auto_now=False)
    type = models.ForeignKey(BatchTestType, on_delete=models.SET("_del"))
    value = models.FloatField()
    description = models.CharField(max_length=250, blank=True)
    units = models.ForeignKey(Unit, on_delete=models.SET("_del"))
    batch = models.ForeignKey(Batch, blank=True, on_delete=models.CASCADE, related_name="tests")

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
            gravTest.value = instance.startingGravity.magnitude
            gravTest.description = "Auto created from new batch."
            gravTest.datetime = datetime.now()
            unit = Unit.objects.filter(name__contains="specific")[0]
            gravTest.units = unit
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
    name = models.ForeignKey(BatchAdditionItem, on_delete=models.SET("_del"))
    description = models.CharField(max_length=250, blank=True, help_text="Add a brief description of this Addition item and why")
    units = models.ForeignKey(Unit, on_delete=models.SET("_del"))
    amount = models.FloatField()
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
            text = "Added [" + instance.name.name + "] :: " + str(instance.amount) + " " + instance.units.name
        else:
            text = "Updated [" + instance.name.name + "]"
    if sender.__name__ == "BatchTest":
        batch = instance.batch
        if created:
            text = "Added [" + instance.type.name + "] :: " + str(instance.value) + " " + instance.units.name
        else:
            text = "Updated [" + instance.type.name + "]"
    if text:
        log = ActivityLog(datetime=date,text=text)
        log.save()
        batch.activity.add(log)

