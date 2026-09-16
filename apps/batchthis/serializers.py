from rest_framework import serializers

from .models import Adjunct, Fermentable, Yeast


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
