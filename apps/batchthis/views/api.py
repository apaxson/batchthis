import logging

from django.db.models import Max
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import Adjunct, Batch, Fermentable, PairingTag, Recipe, Vessel, Yeast
from ..serializers import (
    AdjunctSerializer, BatchSerializer, FermentableSerializer, PairingTagSerializer, RecipeSerializer,
    VesselSerializer, YeastSerializer,
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
