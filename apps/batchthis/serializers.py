from rest_framework import serializers

from .models import Fermentable


class FermentableSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = Fermentable
        fields = ['id', 'display_name', 'type', 'supplier']

    def get_display_name(self, obj: Fermentable) -> str:
        return obj.display_name
