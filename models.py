from django.db import models
from datetime import datetime
from django.dispatch import receiver
from django.db.models.signals import post_save
from django.utils.text import slugify


# Create your models here.
class Unit(models.Model):
    def __str__(self):
        return self.label
    identifier = models.CharField(max_length=10, help_text="Enter the unit identifier, i.e. 'mgL' or 'ph'")
    label = models.CharField(max_length=25, null=True, help_text="Enter abbreviation label of the measured unit, i.e. 'mg/L'")

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

    def complete(self):
        self.enddate = datetime.now()
        self.active = False

# If a batch is saved on a fermenter that isn't currently active,
# set it active
@receiver(post_save,sender=Batch)
def setActiveFermenter(sender,instance,**kwargs):
    fermenters = instance.fermenter.all()
    for fermenter in fermenters:
        if fermenter.status != fermenter.STATUS_ACTIVE:
            fermenter.status = fermenter.STATUS_ACTIVE
            fermenter.save()

class BatchTest(models.Model):
    def __str__(self):
        fmt = "%m/%d/%y-%H:%M"
        return self.datetime.strftime(fmt) + " " + self.type.name

    datetime = models.DateTimeField(auto_now=True)
    type = models.ForeignKey(BatchTestType, on_delete=models.SET("_del"))
    value = models.FloatField()
    description = models.CharField(max_length=250, blank=True)
    units = models.ForeignKey(Unit, on_delete=models.SET("_del"))
    batch = models.ForeignKey(Batch, blank=True, on_delete=models.CASCADE, related_name="tests")



class BatchNote(models.Model):
    def __str__(self):
        fmt = "%m/%d/%y-%H:%M"
        return self.date.strftime(fmt) + " " + self.text[:50]

    text = models.TextField()
    date = models.DateTimeField(auto_now_add=True)
    notetype = models.ForeignKey(BatchNoteType,on_delete=models.SET("_del"))
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE,related_name="notes")