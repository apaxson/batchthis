import logging

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import Fermentable
from ..serializers import FermentableSerializer

logger = logging.getLogger(__name__)


class FermentableListAPIView(APIView):
    def get(self, request: Request) -> Response:
        fermentables = Fermentable.objects.select_related('type').all()
        serializer = FermentableSerializer(fermentables, many=True)
        logger.debug("Listed %d fermentables for %s", len(serializer.data), request.user)
        return Response(serializer.data)
