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
    TOSNA_FERMAID,
    apply_tosna_to_recipe,
    quantity_label,
    recipe_primary_yeast,
    recipe_scale_factor,
    scaled_recipe_ingredients,
    time_to_add_label,
    tosna_schedule,
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


def _tosna_json(schedule) -> dict:
    return {
        'brix': round(schedule.brix, 1),
        'yan_ppm': round(schedule.yan_ppm),
        'gallons': round(schedule.gallons, 3),
        'total_g': round(schedule.total_g, 2),
        'per_addition_g': round(schedule.per_addition_g, 2),
        'break_sg': f"{schedule.break_sg:.3f}" if schedule.break_sg is not None else None,
        'additions': [{'number': a.number, 'when': a.when, 'grams': round(a.grams, 2), 'note': a.note}
                      for a in schedule.additions],
    }


class TosnaCalcAPIView(APIView):
    """The TOSNA 2.0 calculator's live result (Tools page and #tosnaModal): ?sg=&size=&nitrogen=[&end_sg=]."""
    def get(self, request: Request) -> Response:
        params = request.query_params
        try:
            size = VolumeField().clean(params.get('size', ''))
            schedule = tosna_schedule(params.get('sg'), size, params.get('nitrogen', ''), end_sg=params.get('end_sg'))
        except ValidationError as e:
            logger.debug("TosnaCalc: rejected %s: %s", dict(params), e.messages)
            return Response({'error': " ".join(e.messages)}, status=400)
        return Response(_tosna_json(schedule))


class RecipeTosnaAPIView(APIView):
    """
    TOSNA on the recipe page: GET = the recipe's inputs (OG, FG, size, its one
    yeast + nitrogen demand) and its current Fermaid O rows, for the pop-up and
    its replace summary; POST nitrogen=&replace=true|false = add the four doses.
    """
    def get(self, request: Request, pk: int) -> Response:
        recipe = get_object_or_404(Recipe, pk=pk)
        try:
            yeast = recipe_primary_yeast(recipe).yeast
            yeast_json, yeast_error = {'name': yeast.name, 'nitrogen': yeast.nitrogen_requirement}, ""
        except ValidationError as e:
            yeast_json, yeast_error = None, " ".join(e.messages)
        current = [{'amount': quantity_label(row.amount), 'when': time_to_add_label(row.time_to_add),
                    'notes': row.recipe_notes or ""}
                   for row in recipe.adjuncts.filter(adjunct__name__iexact=TOSNA_FERMAID).order_by('pk')]
        logger.debug("RecipeTosna: recipe=%s yeast=%s current=%d", pk, yeast_json, len(current))
        return Response({
            'sg': f"{recipe.estOG.magnitude:.3f}" if recipe.estOG is not None else "",
            'end_sg': f"{recipe.estFG.magnitude:.3f}" if recipe.estFG is not None else "",
            'size': quantity_label(recipe.batchSize),
            'yeast': yeast_json, 'yeast_error': yeast_error,
            'current': current,
        })

    def post(self, request: Request, pk: int) -> Response:
        recipe = get_object_or_404(Recipe, pk=pk)
        nitrogen = request.data.get('nitrogen', '')
        replace = str(request.data.get('replace', '')).lower() in ('true', '1', 'yes', 'on')
        try:
            schedule = tosna_schedule(recipe.estOG.magnitude if recipe.estOG is not None else None, recipe.batchSize,
                                      nitrogen, end_sg=recipe.estFG.magnitude if recipe.estFG is not None else None)
            apply_tosna_to_recipe(recipe, schedule, nitrogen, replace=replace)
        except ValidationError as e:
            logger.debug("RecipeTosna: recipe=%s rejected: %s", pk, e.messages)
            return Response({'error': " ".join(e.messages)}, status=400)
        except Exception:
            logger.exception("RecipeTosna: failed to apply TOSNA to recipe %s", pk)
            return Response({'error': "Couldn't add the TOSNA additions. Nothing was saved - please try again."},
                            status=500)
        return Response({'saved': True})
