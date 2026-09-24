from django.contrib import admin
from .models import Unit, Fermenter, BatchNoteType, BatchTestType, BatchNote, BatchTest, BatchCategory, BatchStyle
from .models import AdjunctType, Adjunct, Yeast, FermentableType, Fermentable, Recipe, AdjunctUsage, BatchStage, BatchStageEvent
from .models import BatchPlanStep, RecipePlanStep, WorkflowTemplate, WorkflowTemplateStep
# Register your models here.

admin.site.register(Unit)
admin.site.register(Fermenter)
# Vessel and VesselStatusEvent are deliberately not registered: status must only
# change through services.set_vessel_status() so the status history stays complete.
admin.site.register(BatchNoteType)
admin.site.register(BatchTestType)
# Batch is deliberately not registered: saving a batch here bypasses the
# workflow (vessel status via set_vessel_status(), and later BatchStage events).
# Create/complete batches through the app views instead.
admin.site.register(BatchNote)
admin.site.register(BatchTest)
admin.site.register(BatchStyle)
admin.site.register(BatchCategory)


class _ViewOnlyAdmin(admin.ModelAdmin):
    # Default for admin registrations: view only. Plans are edited on the app's pages.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WorkflowTemplate)
class WorkflowTemplateAdmin(_ViewOnlyAdmin):
    list_display = ('name', 'description')


@admin.register(WorkflowTemplateStep)
class WorkflowTemplateStepAdmin(_ViewOnlyAdmin):
    list_display = ('template', 'sort_order', 'stage', 'planned_duration', 'vessel_type')
    list_filter = ('template',)


@admin.register(RecipePlanStep)
class RecipePlanStepAdmin(_ViewOnlyAdmin):
    list_display = ('recipe', 'sort_order', 'stage', 'planned_duration', 'vessel_type')
    list_filter = ('recipe',)


@admin.register(BatchPlanStep)
class BatchPlanStepAdmin(_ViewOnlyAdmin):
    list_display = ('batch', 'sort_order', 'stage', 'planned_duration', 'vessel_type')
    list_filter = ('batch',)


@admin.register(BatchStageEvent)
class BatchStageEventAdmin(admin.ModelAdmin):
    # View-only (default for admin registrations): events are logged by the
    # stage-logging service, which also transfers/completes the batch.
    list_display = ('timestamp', 'batch', 'stage', 'vessel', 'notes')
    list_filter = ('stage',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BatchStage)
class BatchStageAdmin(admin.ModelAdmin):
    # View-only: the workflow graph is fixed (seeded by 0035_default_load2) and code
    # looks stages up by shortid, so editing rows here would break the workflow.
    list_display = ('sort_order', 'name', 'shortid', 'from_state', 'to_state', 'transfers_batch', 'description')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(AdjunctType)
admin.site.register(AdjunctUsage)
admin.site.register(Adjunct)
admin.site.register(Yeast)
admin.site.register(FermentableType)
admin.site.register(Fermentable)
admin.site.register(Recipe)