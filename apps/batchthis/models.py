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

from django.db import models
from datetime import datetime
from django.dispatch import receiver
from django.db.models.signals import post_save, m2m_changed
from django.utils.text import slugify
from django.core.files.storage import FileSystemStorage
from pint import Quantity
from quantityfield.fields import QuantityField
from .fields import DescriptiveQuantityField
import logging

logger = logging.getLogger(__name__)


#TODO Refactor "fermenter" to generic "Vessel" and add "Vessel use" to be 'fermenter','aging','serving',etc.
#TODO add "Packaging" to identify how the batch was finished

batch_stages = [
    ('PRI', "Primary Fermentation"),
    ('SEC', "Secondary Fermentation"),
    ('TER', "Tertiary Fermentation"),
    ('RAC', "Racking"),
    ('AGE', " Long-term Aging")
]

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
    name = models.CharField(max_length=20)

    def __str__(self):
        return self.name

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

    def __str__(self):
        return self.name + " (" + str(self.max_size) + self.max_size_units.identifier + ")"
    name = models.CharField(max_length=25)
    max_size = models.IntegerField()
    max_size_units = models.ForeignKey(Unit, related_name="fermenter_max_size_units", on_delete=models.SET("_del"))
    used_size = models.IntegerField(blank=True, null=True)
    used_size_units = models.ForeignKey(Unit, blank=True, null=True,related_name="fermenter_used_size_units", on_delete=models.SET("_del"))
    status = models.CharField(max_length=15, default=STATUS_READY)
    intended_use = models.CharField(max_length=30)


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
    form = models.CharField(max_length=20, choices=(('dry','Dry'),('liquid','Liquid')))
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


class Batch(models.Model):

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'batches'

    name = models.CharField(max_length=50)
    startdate = models.DateTimeField(auto_now=True)
    enddate = models.DateTimeField(null=True, blank=True)
    lotId = models.CharField(max_length=7, null=True) # Bottledate L[2digityear][0paddedYearDays] = L22088
    size = DescriptiveQuantityField(base_units='liters', unit_choices=['liters','gallons'])
    active = models.BooleanField(default=True)
    fermenter = models.ForeignKey(Fermenter, on_delete=models.RESTRICT)
    startingGravity = QuantityField(base_units="sg")
    estimatedEndGravity = QuantityField(base_units="sg")
    category = models.ForeignKey(BatchCategory, on_delete=models.RESTRICT, blank=True, null=True)
    activity = models.ManyToManyField(ActivityLog, blank=True, related_name='batch')
    recipe = models.ForeignKey(Recipe, on_delete=models.RESTRICT, null=True, blank=True)
    # TODO Add additional objects
    aging_vessel = None
    packaging = None
    # TODO Add pre_save signal to compare the two objects for fields changed.

    def transfer(self,src_vessel, dst_vessel):
        pass

    def complete(self):
        self.enddate = datetime.now()
        self.active = False

    def current_gravity(self):
        gravity_tests = self.tests.filter(type__shortid='specific-gravity')
        if len(gravity_tests) > 1:
            return gravity_tests.last().value
        return gravity_tests[0].value


    def percent_complete(self):
        est_fg = self.estimatedEndGravity.magnitude
        current_gravity = self.current_gravity()
        return round((self.startingGravity.magnitude - current_gravity) / (self.startingGravity.magnitude - est_fg) * 100)


# If a batch is saved on a fermenter that isn't currently active,
# set it active
@receiver(post_save, sender=Batch)
def setActiveFermenter(sender,instance,**kwargs):
    logger.debug(f"Received Batch save.  Instance: {instance} \n Sender: {sender}")
    if instance.active:
        if instance.fermenter.vessel.status != Vessel.STATUS_ACTIVE:
            instance.fermenter.vessel.status = Vessel.STATUS_ACTIVE
            instance.fermenter.vessel.save()
            logger.info(f"Received Batch Save.  Moved Fermenter '{instance.fermenter.vessel.name}' to ACTIVE")
            logger.debug(f"Batch Saved:  Instance: {instance} Fermenter: {instance.fermenter} Fermenter State: {instance.fermenter.vessel.status}")
    if not instance.active:
        #TODO this is a bad way to do this.  This implies any edits to an inactive batch will cause the assigned fermenter to DIRTY
        # Use a workflow or state-engine in the future.
        if instance.fermenter.vessel.status is Vessel.STATUS_ACTIVE:
            instance.fermenter.vessel.status = Vessel.STATUS_DIRTY
            instance.fermenter.save()
            logger.info(f"Received Batch Save.  Moved Fermenter '{instance.fermenter.vessel.name}' to DIRTY")


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
            gravTest.value = instance.startingGravity
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

