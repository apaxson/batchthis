from django.db import models
from datetime import datetime
from django.dispatch import receiver
from django.db.models.signals import post_save, m2m_changed
from django.utils.text import slugify


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
@receiver(post_save,sender=Unit)
def print_unit_save(sender,instance,**kwargs):
    print("Received save from a Unit: " + instance.label + "\n" + str(kwargs))

class Fermenter(models.Model):
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

class ActivityLog(models.Model):
    datetime = models.DateTimeField()
    text = models.TextField()

class Batch(models.Model):

    def __str__(self):
        return self.name

    class Meta:
        #ordering = ['-startdate']
        verbose_name_plural = 'batches'

    name = models.CharField(max_length=50)
    startdate = models.DateTimeField(auto_now=True)
    enddate = models.DateTimeField(null=True, blank=True)
    size = models.IntegerField()
    size_units = models.ForeignKey(Unit, on_delete=models.SET("_del"))
    active = models.BooleanField(default=True)
    # Using the 'related_name' on batches, applies that name on the other side for "Fermenter.batch"
    fermenter = models.ManyToManyField(Fermenter, blank=True, related_name='batch')
    startingGravity = models.FloatField()
    estimatedEndGravity = models.FloatField()
    category = models.ForeignKey(BatchCategory, on_delete=models.SET("_del"), blank=True, null=True)
    activity = models.ManyToManyField(ActivityLog, related_name='batch')

    def complete(self):
        self.enddate = datetime.now()
        self.active = False

# If a batch is saved on a fermenter that isn't currently active,
# set it active
@receiver(m2m_changed,sender=Batch.fermenter.through)
def setActiveFermenter(sender,instance,**kwargs):
    fermenters = instance.fermenter.all()
    for fermenter in fermenters:
        if fermenter.status != Fermenter.STATUS_ACTIVE:
            fermenter.status = fermenter.STATUS_ACTIVE
            fermenter.save()

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

# If a batch is saved with a Starting Gravity, add that test
@receiver(post_save,sender=Batch)
def addGravityTest(sender,instance,**kwarts):
    print("Adding Gravity")
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
        print("Saving gravity")
        gravTest.save()

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
            text = "Added " + instance.name.name + ":" + str(instance.amount) + " " + instance.units.name
        else:
            text = "Updated " + instance.name.name
    if sender.__name__ == "BatchTest":
        batch = instance.batch
        if created:
            text = "Added " + instance.type.name + ":" + str(instance.value) + " " + instance.units.name
        else:
            text = "Updated " + instance.type.name
    if text:
        log = ActivityLog(datetime=date,text=text)
        log.save()
        batch.activity.add(log)