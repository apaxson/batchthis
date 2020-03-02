from django.contrib import admin
from .models import Unit, Fermenter, BatchNoteType, BatchTestType, Batch, BatchNote, BatchTest
# Register your models here.

admin.site.register(Unit)
admin.site.register(Fermenter)
admin.site.register(BatchNoteType)
admin.site.register(BatchTestType)
admin.site.register(Batch)
admin.site.register(BatchNote)
admin.site.register(BatchTest)