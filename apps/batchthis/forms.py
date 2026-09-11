import pdb

from django.forms import ModelForm, inlineformset_factory
from django.db.models import Q, CharField
from django import forms
from django.core.exceptions import ValidationError

import apps.batchthis.models
from .models import BatchTest, BatchNote, BatchAddition, Batch, Unit, Fermenter, Vessel, BatchCategory, BatchStyle
from .models import Fermentable, Adjunct, Yeast, Recipe, AdjunctUsage, RecipeFermentable
from django.forms.widgets import NumberInput, DateInput
from quantityfield.fields import QuantityFormField, QuantityWidget
from .fields import DescriptiveQuantityFormField, PrecisionQuantityWidget, PrecisionTextWidget
import logging

logger = logging.getLogger(__name__)

class DateTimeWidget(forms.DateTimeInput):
    input_type = "datetime-local"
    def __init__(self,**kwargs):
        kwargs["format"] = "%Y/%m/%dT%H:%M"
        super().__init__(**kwargs)

class BatchTestForm(ModelForm):
    class Meta:
        model = BatchTest
        fields = "__all__"

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields["datetime"].widget = DateTimeWidget()
        self.fields["datetime"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"]

class BatchNoteForm(ModelForm):
    class Meta:
        model = BatchNote
        fields = "__all__"

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields["date"].widget = DateTimeWidget()
        self.fields["date"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"]

class BatchAdditionForm(ModelForm):
    class Meta:
        model = BatchAddition
        fields = "__all__"

class BatchAddForm(forms.Form):
    name = forms.CharField(widget=forms.TextInput(attrs={'placeholder':'Name of Batch'}),required=True)
    startdate = forms.DateField(label="Start Date",widget=DateInput(attrs={'type':'date'}),required=True)
    size = forms.CharField(widget=forms.TextInput(attrs={'placeholder':'i.e. 6 gallons'}),label = "Batch Size", required=True)
    fermenter = forms.ModelChoiceField(queryset=Fermenter.objects.all())
    startingGravity = forms.CharField(widget=PrecisionTextWidget(precision=3, base_units='sg'), label="Starting Gravity", required=True)
    estimatedEndGravity = forms.CharField(widget=PrecisionTextWidget(precision=3, base_units='sg'), label="Estimated End Gravity", required=True)
    recipe = forms.ModelChoiceField(queryset=Recipe.objects.all())

# class BatchForm(forms.ModelForm):
#     class Meta:
#         model = Batch
#         fields = ['name','startdate','size','fermenter','startingGravity','estimatedEndGravity','recipe']
#         widgets = {
#             'name': forms.TextInput(attrs={'placeholder':'Name of Batch'}),
#             'size': forms.TextInput(attrs={'placeholder':'i.e. 6 gallons'}),
#             'startingGravity': PrecisionTextWidget(precision=3, base_units='sg'),
#             'estimatedEndGravity': PrecisionTextWidget(precision=3, base_units='sg'),
#         }


class RecipeImportForm(forms.Form):
    file = forms.FileField()


class RecipeFormItem(forms.Form):
    intended_use_id = forms.ModelChoiceField(queryset=AdjunctUsage.objects.all(), label="Usage")
    amount = forms.CharField(widget=forms.TextInput(), label = "Amount", required=True)
    recipe_notes = forms.CharField(widget=forms.Textarea, required=False)

    """def clean(self):
        cleaned_data = super().clean()
        amount_weight = cleaned_data.get('amount_weight')
        amount_volume = cleaned_data.get('amount_volume')
        print("form clean(): ")
        #FIXME: If both fields are empty, rather than displaying "required" it says "can't have BOTH"
        if amount_weight is None and amount_volume is None:
            raise ValidationError({'amount_weight': "Pick one", 'amount_volume': 'Pick one'})
        if (amount_weight is not None) is (amount_volume is not None):
            # XOR. We can have one or the other but not both
            raise ValidationError({'amount_weight': 'Cannot have BOTH weight and volume',
                                   'amount_volume': 'Cannot have BOTH weight and volume'})
    """
class FermentableForm(RecipeFormItem):

    #TODO use chosen.js (Django-chosen) to select a fermentable rather than a ModelChoiceField
    fermentable_id = forms.ModelChoiceField(queryset=Fermentable.objects.all(), label = "Fermentable")
    is_fermentable = forms.BooleanField(label="Is Fermentable?", initial=True,
                                        required=False)

class AdjunctForm(RecipeFormItem):

    adjunct_id = forms.ModelChoiceField(queryset=Adjunct.objects.all())
    time_to_add = forms.CharField(widget=forms.TextInput(attrs={'placeholder':'Time in days/hours/mins from start'}), label="Time to Add", required=True)

class YeastForm(forms.Form):
    yeast_id = forms.ModelChoiceField(queryset=Yeast.objects.all())
    amount = forms.CharField(widget=forms.TextInput,
                                     label = "Amount",
                                     required=True)
    notes = forms.CharField(widget=forms.Textarea, required=False)

class RecipeAddForm(forms.Form):
    class Meta:
        model = Recipe

    file = forms.FileField(widget=forms.FileInput(), label="Recipe File", required=False)
    name = forms.CharField(widget=forms.TextInput(attrs={'placeholder':'Recipe Name'}),required=True,label="Recipe Name")
    dateCreated = forms.DateField(label="Created Date",widget=NumberInput(attrs={'type':'date'}),required=True)
    dateUpdated = forms.DateField(label="Last Updated",widget=NumberInput(attrs={'type':'date'}), required=False)
    version = forms.IntegerField(label="Version", required=False)
    style = forms.ModelChoiceField(queryset=BatchStyle.objects.all())
    category = forms.ModelChoiceField(queryset=BatchCategory.objects.all())
    brewer = forms.CharField(widget=forms.TextInput(attrs={'placeholder':'Name of brewer'}),label="Brewer", required=False)
    batchSize = forms.CharField(widget=forms.TextInput(attrs={'placeholder': 'Size of Batch'}), label = "Batch Size", required=True)
    notes = forms.CharField(widget=forms.Textarea(),label="Recipe Notes")
    estOG = QuantityFormField(widget=PrecisionTextWidget(precision=3),base_units='sg', required=True, label="Expected Original Gravity")
    estFG = forms.CharField(widget=PrecisionTextWidget(precision=3, base_units='sg'), required=True,
                              label="Estimated Final Gravity")
    estABV = forms.FloatField(widget=forms.TextInput(attrs={'placeholder':'i.e. 11.4'}),label="Estimated ABV in %", required=True)

class RefractometerCorrectionForm(forms.Form):
    unitChoices = (
        ('sg', "Specific Gravity"),
        ('bx', "Brix")
    )
    startData = forms.FloatField(help_text="Enter starting measurement",
                                 label="Starting Measurement",
                                 initial=0.0)
    startUnit = forms.ChoiceField(choices=unitChoices, help_text="Choose starting unit type of measurement",
                                  label="Starting Measurement Unit")
    currentData = forms.FloatField(help_text="Enter current measurement",
                                   label="Current Measurement",
                                   initial=0.0)
    currentUnit = forms.ChoiceField(choices=unitChoices, help_text="Choose current unit type of measurement",
                                    label="Current Measurement Unit")