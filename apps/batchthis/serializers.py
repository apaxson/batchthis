from django.urls import reverse
from django.utils import timezone
from rest_framework import serializers

from .models import Adjunct, Batch, Fermentable, Recipe, Vessel, Yeast


class FermentableSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = Fermentable
        fields = ['id', 'display_name', 'type', 'supplier']

    def get_display_name(self, obj: Fermentable) -> str:
        return obj.display_name


class AdjunctSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = Adjunct
        fields = ['id', 'display_name', 'type', 'supplier']

    def get_display_name(self, obj: Adjunct) -> str:
        return obj.display_name


class YeastSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = Yeast
        fields = ['id', 'display_name', 'type', 'supplier']

    def get_display_name(self, obj: Yeast) -> str:
        return obj.display_name


class RecipeSerializer(serializers.ModelSerializer):
    style = serializers.SerializerMethodField()
    category = serializers.SerializerMethodField()
    bjcp_code = serializers.SerializerMethodField()
    detail_url = serializers.SerializerMethodField()

    class Meta:
        model = Recipe
        fields = ['id', 'name', 'style', 'category', 'bjcp_code', 'estABV', 'brewer', 'detail_url']

    def get_style(self, obj: Recipe) -> str:
        return obj.category.style.name if obj.category else ''

    def get_category(self, obj: Recipe) -> str:
        return obj.category.name if obj.category else ''

    def get_bjcp_code(self, obj: Recipe) -> str:
        return obj.category.bjcp_code if obj.category else ''

    def get_detail_url(self, obj: Recipe) -> str:
        return reverse('recipe', kwargs={'pk': obj.pk})


class BatchSerializer(serializers.ModelSerializer):
    style = serializers.SerializerMethodField()
    category = serializers.SerializerMethodField()
    fermenter = serializers.SerializerMethodField()
    size = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    status_class = serializers.SerializerMethodField()
    startdate = serializers.DateTimeField(format='%Y-%m-%d')
    enddate = serializers.DateTimeField(format='%Y-%m-%d')
    detail_url = serializers.SerializerMethodField()

    class Meta:
        model = Batch
        fields = ['id', 'name', 'style', 'category', 'fermenter', 'size', 'status', 'status_class',
                  'startdate', 'enddate', 'detail_url']

    def _effective_category(self, obj: Batch):
        if obj.category:
            return obj.category
        if obj.recipe and obj.recipe.category:
            return obj.recipe.category
        return None

    def get_style(self, obj: Batch) -> str:
        category = self._effective_category(obj)
        return category.style.name if category else ''

    def get_category(self, obj: Batch) -> str:
        category = self._effective_category(obj)
        return str(category) if category else ''

    def get_fermenter(self, obj: Batch) -> str:
        return obj.fermenter.vessel.name

    def get_size(self, obj: Batch) -> str:
        return str(obj.size)

    def get_status(self, obj: Batch) -> str:
        return 'Active' if obj.active else 'Complete'

    def get_status_class(self, obj: Batch) -> str:
        return 'cl-stage--active' if obj.active else 'cl-stage--complete'

    def get_detail_url(self, obj: Batch) -> str:
        return reverse('batch', kwargs={'pk': obj.pk})


class VesselSerializer(serializers.ModelSerializer):
    """
    Expects the queryset annotated with `status_since` (latest status event
    timestamp) and a `current_batches` context dict {vessel_id: batch name}
    - see VesselListAPIView - so the list page stays at a fixed query count.
    """
    vessel_type = serializers.CharField(read_only=True)
    capacity = serializers.SerializerMethodField()
    status_class = serializers.CharField(source='status_badge_class', read_only=True)
    current_batch = serializers.SerializerMethodField()
    status_since = serializers.SerializerMethodField()
    detail_url = serializers.SerializerMethodField()
    # Label for the react-select vessel picker (ModelSelect.jsx reads display_name).
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = Vessel
        fields = ['id', 'name', 'vessel_type', 'capacity', 'status', 'status_class', 'current_batch',
                  'status_since', 'intended_use', 'detail_url', 'display_name']

    def get_capacity(self, obj: Vessel) -> str:
        return f"{obj.max_size} {obj.max_size_units.identifier}"

    def get_current_batch(self, obj: Vessel) -> str:
        return self.context.get('current_batches', {}).get(obj.pk, '')

    def get_status_since(self, obj: Vessel):
        since = getattr(obj, 'status_since', None)
        return timezone.localtime(since).strftime('%Y-%m-%d') if since else None

    def get_detail_url(self, obj: Vessel) -> str:
        return reverse('vessel', kwargs={'pk': obj.pk})

    def get_display_name(self, obj: Vessel) -> str:
        details = ", ".join(part for part in (obj.vessel_type, self.get_capacity(obj)) if part)
        return f"{obj.name} ({details})"
