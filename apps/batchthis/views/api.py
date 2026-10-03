import logging

from django.core.exceptions import ValidationError
from django.db.models import Max
from django.shortcuts import get_object_or_404
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ..fields import VolumeField
from ..models import Adjunct, Batch, Fermentable, PairingTag, Recipe, Vessel, Yeast
from ..serializers import (
    AdjunctSerializer,
    BatchSerializer,
    FermentableSerializer,
    PairingTagSerializer,
    RecipeSerializer,
    VesselSerializer,
    YeastSerializer,
)
from ..services import (
    quantity_label,
    recipe_scale_factor,
    scaled_recipe_ingredients,
    time_to_add_label,
)

logger = logging.getLogger(__name__)


class FermentableListAPIView(APIView):
    def get(self, request: Request) -> Response:
        fermentables = Fermentable.objects.select_related('type').all()
        serializer = FermentableSerializer(fermentables, many=True)
        logger.debug("Listed %d fermentables for %s", len(serializer.data), request.user)
        return Response(serializer.data)


class AdjunctListAPIView(APIView):
    def get(self, request: Request) -> Response:
        adjuncts = Adjunct.objects.select_related('type').all()
        serializer = AdjunctSerializer(adjuncts, many=True)
        logger.debug("Listed %d adjuncts for %s", len(serializer.data), request.user)
        return Response(serializer.data)


class YeastListAPIView(APIView):
    def get(self, request: Request) -> Response:
        yeasts = Yeast.objects.all()
        serializer = YeastSerializer(yeasts, many=True)
        logger.debug("Listed %d yeasts for %s", len(serializer.data), request.user)
        return Response(serializer.data)


class RecipeListAPIView(APIView):
    def get(self, request: Request) -> Response:
        recipes = Recipe.objects.select_related('category', 'category__style').all()
        serializer = RecipeSerializer(recipes, many=True)
        logger.debug("Listed %d recipes for %s", len(serializer.data), request.user)
        return Response(serializer.data)


class BatchListAPIView(APIView):
    def get(self, request: Request) -> Response:
        batches = Batch.objects.select_related(
            'category', 'category__style', 'recipe__category', 'recipe__category__style', 'fermenter__vessel'
        ).all()
        serializer = BatchSerializer(batches, many=True)
        logger.debug("Listed %d batches for %s", len(serializer.data), request.user)
        return Response(serializer.data)


class VesselListAPIView(APIView):
    def get(self, request: Request) -> Response:
        vessels = (
            Vessel.objects
            .prefetch_related('fermenter_set', 'agingtank_set', 'barrel_set')
            .annotate(status_since=Max('status_events__timestamp'))
            .order_by('name')
        )
        # ?status=Clean/Ready - e.g. the stage form's destination picker.
        status = request.query_params.get('status')
        if status:
            vessels = vessels.filter(status=status)
        # One query for every vessel's active batch, instead of one per row.
        # Batch.vessel is unset on batches older than it; those are still in their fermenter.
        current_batches = {
            vessel_id or fermenter_vessel_id: name
            # A packaged batch (Bottles / Kegs) is in no vessel - not its starting fermenter.
            for vessel_id, fermenter_vessel_id, name in Batch.objects.filter(active=True).exclude(
                vessel__isnull=True, packaging__gt=''
            ).values_list(
                'vessel_id', 'fermenter__vessel_id', 'name'
            )
        }
        serializer = VesselSerializer(vessels, many=True, context={'current_batches': current_batches})
        logger.debug("Listed %d vessels for %s", len(serializer.data), request.user)
        return Response(serializer.data)


class PairingTagListAPIView(APIView):
    """Every food pairing tag, alphabetically - the recipe form's tag picker suggests from these."""
    def get(self, request: Request) -> Response:
        serializer = PairingTagSerializer(PairingTag.objects.all(), many=True)
        logger.debug("Listed %d pairing tags for %s", len(serializer.data), request.user)
        return Response(serializer.data)


class RecipeScaledAPIView(APIView):
    """
    Add batch's ingredient preview: the recipe's ingredients scaled to ?size=
    (RECIPE SCALING) - the same rows Add batch will copy onto the batch.
    """
    def get(self, request: Request, pk: int) -> Response:
        recipe = get_object_or_404(Recipe, pk=pk)
        try:
            size = VolumeField().clean(request.query_params.get('size', ''))
        except ValidationError as e:
            logger.debug("RecipeScaled: recipe=%s bad size %r: %s", pk, request.query_params.get('size'), e.messages)
            return Response({'error': " ".join(e.messages)}, status=400)
        factor = recipe_scale_factor(recipe, size)
        rows = scaled_recipe_ingredients(recipe, size)
        logger.debug("RecipeScaled: recipe=%s size=%s factor=%s rows=%d", pk, size, factor, len(rows))
        return Response({
            'factor': factor,
            'recipe_size': quantity_label(recipe.batchSize),
            'batch_size': quantity_label(size),
            'ingredients': [{
                'kind': row.get_kind_display(),
                'name': row.name,
                'recipe_amount': quantity_label(row.recipe_amount),
                'amount': quantity_label(row.amount),
                'time_to_add': time_to_add_label(row.time_to_add) if row.kind == row.KIND_ADJUNCT else "",
            } for row in rows],
        })
