from django.urls import reverse
from rest_framework import serializers

from .models import Adjunct, Fermentable, Recipe, Yeast


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
