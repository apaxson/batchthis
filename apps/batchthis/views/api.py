import logging

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import Adjunct, Batch, Fermentable, Recipe, Yeast
from ..serializers import AdjunctSerializer, BatchSerializer, FermentableSerializer, RecipeSerializer, YeastSerializer

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
