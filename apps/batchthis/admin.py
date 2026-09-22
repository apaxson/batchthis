from django.contrib import admin
from .models import Unit, Vessel, VesselStatusEvent, Fermenter, BatchNoteType, BatchTestType, BatchNote, BatchTest, BatchCategory, BatchStyle
from .models import AdjunctType, Adjunct, Yeast, FermentableType, Fermentable, Recipe, AdjunctUsage, BatchStage
# Register your models here.

admin.site.register(Unit)
admin.site.register(Fermenter)
admin.site.register(Vessel)
admin.site.register(VesselStatusEvent)
admin.site.register(BatchNoteType)
admin.site.register(BatchTestType)
# Batch is deliberately not registered: saving a batch here bypasses the
# workflow (vessel status via set_vessel_status(), and later BatchStage events).
# Create/complete batches through the app views instead.
admin.site.register(BatchNote)
admin.site.register(BatchTest)
admin.site.register(BatchStyle)
admin.site.register(BatchCategory)
admin.site.register(BatchStage)
admin.site.register(AdjunctType)
admin.site.register(AdjunctUsage)
admin.site.register(Adjunct)
admin.site.register(Yeast)
admin.site.register(FermentableType)
admin.site.register(Fermentable)
admin.site.register(Recipe)