import pdb
import datetime
import json

from django.forms import ModelForm, inlineformset_factory
from django.db.models import Q, CharField
from django import forms
from django.core.exceptions import ValidationError

import apps.batchthis.models
from .models import BatchTest, BatchNote, BatchAddition, Batch, Unit, Fermenter, Vessel, BatchCategory, BatchStyle
from .models import BatchStage, BatchTestType, READING_SPECS
from .services import allowed_next_stages, vessel_name_problem, VESSEL_TYPES
from .models import Fermentable, Adjunct, Yeast, Recipe, AdjunctUsage, RecipeFermentable
from django.forms.widgets import NumberInput, DateInput
from django.utils import timezone
from pint import Quantity
from quantityfield.fields import QuantityFormField, QuantityWidget
from .fields import AmountField, DescriptiveQuantityFormField, PrecisionQuantityWidget, PrecisionTextWidget, ReadingField, VolumeField
import logging

logger = logging.getLogger(__name__)

class DateTimeWidget(forms.DateTimeInput):
    input_type = "datetime-local"
    def __init__(self,**kwargs):
        # datetime-local only accepts YYYY-MM-DDTHH:MM; anything else renders blank.
        kwargs["format"] = "%Y-%m-%dT%H:%M"
        super().__init__(**kwargs)

class BatchTestForm(ModelForm):
    # No `batch` field: the view attaches the reading to the batch in the URL.
    # The value's unit is checked against the chosen test type (READING_SPECS).
    value = ReadingField(label="Value")

    class Meta:
        model = BatchTest
        fields = ['datetime', 'type', 'value', 'description']

    def __init__(self, *args, batch: Batch | None = None, **kwargs):
        # The batch's starting gravity corrects a Brix reading for alcohol.
        self.batch = batch
        super().__init__(*args, **kwargs)
        self.fields["datetime"].widget = DateTimeWidget()
        self.fields["datetime"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"]

    def clean(self):
        cleaned = super().clean()
        test_type, value = cleaned.get('type'), cleaned.get('value')
        spec = READING_SPECS.get(test_type.shortid) if test_type else None
        if spec is None:
            return cleaned
        errors = self.errors.get('value')
        if errors and errors.as_data()[0].code == 'invalid_amount':
            # Word the "not a number" error for the chosen type.
            self.errors['value'] = self.error_class([f"Enter a number, e.g. {spec.example}."])
        elif value is not None:
            problem = spec.problem(test_type.name, value)
            if problem:
                self.add_error('value', ValidationError(problem[1], code=problem[0]))
            else:
                start_sg = self.batch.startingGravity.magnitude if self.batch and self.batch.startingGravity else None
                cleaned['value'], note = spec.normalize(value, start_sg=start_sg)
                if note:
                    # Keep the Brix that was actually read, next to any description typed.
                    description = (cleaned.get('description') or '').strip()
                    cleaned['description'] = (f"{description} - {note}" if description
                                              else note[0].upper() + note[1:])[:250]
        return cleaned

    @property
    def reading_examples_json(self) -> str:
        """{test type pk: placeholder} as JSON, for addTest.html's placeholder switch."""
        return json.dumps({str(t.pk): f"e.g. {READING_SPECS[t.shortid].example}"
                           for t in BatchTestType.objects.filter(shortid__in=READING_SPECS)}, ensure_ascii=False)

class BatchNoteForm(ModelForm):
    # No `batch` field: the view attaches the note to the batch in the URL.
    class Meta:
        model = BatchNote
        fields = ['date', 'notetype', 'text']

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields["date"].widget = DateTimeWidget()
        self.fields["date"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"]

class BatchAdditionForm(ModelForm):
    # No `batch` field: the view attaches the addition to the batch in the URL.
    # The adjunct is picked with the react-select picker (fills this hidden input).
    adjunct = forms.ModelChoiceField(
        queryset=Adjunct.objects.all(), widget=forms.HiddenInput(),
        error_messages={'required': "Choose an adjunct."},
    )
    amount = AmountField()

    class Meta:
        model = BatchAddition
        fields = ['adjunct', 'amount', 'description']

    def full_clean(self):
        super().full_clean()
        # Django doesn't mark hidden inputs aria-invalid; do it so the picker gets
        # the red border (see .model-select__control in cellar-ledger.css).
        if 'adjunct' in self.errors:
            self.fields['adjunct'].widget.attrs['aria-invalid'] = 'true'

class BatchAddForm(forms.Form):
    name = forms.CharField(widget=forms.TextInput(attrs={'placeholder':'Name of Batch'}),required=True)
    startdate = forms.DateField(label="Start Date",widget=DateInput(attrs={'type':'date'}),required=True)
    size = VolumeField(label="Batch Size", placeholder="i.e. 6 gallons")
    # A new batch can only go into a Clean/Ready vessel; enforced on POST, not
    # just by what the dropdown shows.
    fermenter = forms.ModelChoiceField(
        queryset=Fermenter.objects.filter(vessel__status=Vessel.STATUS_READY),
        error_messages={'invalid_choice': f"That fermenter isn't {Vessel.STATUS_READY}. Pick a clean, ready vessel."},
    )
    startingGravity = forms.CharField(widget=PrecisionTextWidget(precision=3, base_units='sg'), label="Starting Gravity", required=True)
    estimatedEndGravity = forms.CharField(widget=PrecisionTextWidget(precision=3, base_units='sg'), label="Estimated End Gravity", required=True)
    recipe = forms.ModelChoiceField(queryset=Recipe.objects.all())

    def clean_startdate(self) -> datetime.datetime:
        # The form only asks for a date, but Batch.startdate is a datetime used for
        # hours-elapsed fault checks: today means right now, an earlier date means
        # the start of that day.
        start = self.cleaned_data['startdate']
        if start == timezone.localdate():
            return timezone.now()
        return timezone.make_aware(datetime.datetime.combine(start, datetime.time.min))

class BatchEditForm(forms.Form):
    """
    Correct an existing batch's details. Deliberately has no fermenter/vessel
    or start date: vessel moves go through Log stage / Transfer batch (so
    vessel status and stage events stay right), and startdate anchors the
    timeline and fault checks.
    """
    name = forms.CharField(max_length=50, widget=forms.TextInput(attrs={'placeholder': 'Name of Batch'}))
    recipe = forms.ModelChoiceField(queryset=Recipe.objects.all(), required=False, empty_label="No recipe")
    size = VolumeField(label="Batch Size", placeholder="i.e. 6 gallons")
    startingGravity = forms.CharField(widget=PrecisionTextWidget(precision=3, base_units='sg'), label="Starting Gravity")
    estimatedEndGravity = forms.CharField(widget=PrecisionTextWidget(precision=3, base_units='sg'), label="Estimated End Gravity")

    def _clean_gravity(self, field: str) -> float:
        try:
            value = float(self.cleaned_data[field])
        except (TypeError, ValueError):
            raise ValidationError("Enter a specific gravity, e.g. 1.090.")
        if value <= 0:
            raise ValidationError("Enter a specific gravity, e.g. 1.090.")
        return value

    def clean_startingGravity(self) -> float:
        return self._clean_gravity('startingGravity')

    def clean_estimatedEndGravity(self) -> float:
        return self._clean_gravity('estimatedEndGravity')


class BatchStageForm(forms.Form):
    """
    Log the next workflow stage for one batch. Only the stages the workflow
    allows next are offered; transition_stage_event() re-checks everything.
    """
    stage = forms.ModelChoiceField(
        queryset=BatchStage.objects.none(),
        error_messages={'invalid_choice': "That stage can't be logged next for this batch."},
    )
    timestamp = forms.DateTimeField(
        label="Date/time",
        widget=DateTimeWidget(),
        input_formats=['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M'],
    )
    # Filled by the react-select picker (ModelSelect.jsx). Only Clean/Ready
    # vessels are valid - enforced here on POST, not just by the picker.
    dst_vessel = forms.ModelChoiceField(
        queryset=Vessel.objects.filter(status=Vessel.STATUS_READY),
        required=False,
        label="Destination vessel",
        widget=forms.HiddenInput(),
        error_messages={'invalid_choice': f"That vessel isn't {Vessel.STATUS_READY}. Pick a clean, ready vessel."},
    )
    notes = forms.CharField(max_length=250, required=False)

    def __init__(self, *args, batch: Batch, **kwargs):
        super().__init__(*args, **kwargs)
        self.batch = batch
        self.fields['stage'].queryset = BatchStage.objects.filter(
            pk__in=[stage.pk for stage in allowed_next_stages(batch)]
        )

    def clean(self):
        cleaned = super().clean()
        stage = cleaned.get('stage')
        if stage is None:
            return cleaned
        if stage.transfers_batch and not cleaned.get('dst_vessel') and 'dst_vessel' not in self.errors:
            self.add_error('dst_vessel', f"{stage.name} transfers the batch - choose a clean, ready destination vessel.")
        elif not stage.transfers_batch:
            # A value left over from switching stages in the form; this stage doesn't move the batch.
            cleaned['dst_vessel'] = None
        return cleaned


class BatchTransferForm(forms.Form):
    """
    Ad-hoc batch transfer outside the workflow (services.transfer_batch()).
    Stage defaults to "no stage change"; the alternatives are the allowed next
    stages that transfer the batch.
    """
    dst_vessel = forms.ModelChoiceField(
        queryset=Vessel.objects.filter(status=Vessel.STATUS_READY),
        label="Destination vessel",
        widget=forms.HiddenInput(),
        error_messages={
            'required': "Choose a clean, ready destination vessel.",
            'invalid_choice': f"That vessel isn't {Vessel.STATUS_READY}. Pick a clean, ready vessel.",
        },
    )
    stage = forms.ModelChoiceField(
        queryset=BatchStage.objects.none(),
        required=False,
        error_messages={'invalid_choice': "That stage can't be logged with a transfer right now."},
    )
    timestamp = forms.DateTimeField(
        label="Date/time", widget=DateTimeWidget(), input_formats=['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M'],
    )
    reason = forms.CharField(
        max_length=250,
        error_messages={'required': "A reason is required to transfer a batch outside the workflow."},
    )

    def __init__(self, *args, batch: Batch, **kwargs):
        super().__init__(*args, **kwargs)
        self.batch = batch
        self.fields['stage'].queryset = BatchStage.objects.filter(
            pk__in=[stage.pk for stage in allowed_next_stages(batch) if stage.transfers_batch]
        )
        state = batch.current_state
        self.fields['stage'].empty_label = f"No stage change (stay in {state})" if state else "No stage change"


class VesselForm(forms.Form):
    """
    Add or edit a vessel. The type is chosen once, on Add; status is never set
    here (new vessels start Clean/Ready). Type-specific fields are only
    required for their type: passivation date (Fermenter), serial and toast
    level (Barrel).
    """
    vessel_type = forms.ChoiceField(label="Type", choices=[(t, t) for t in VESSEL_TYPES])
    name = forms.CharField(max_length=25)
    capacity = VolumeField(placeholder="e.g. 6 gallons")
    fill = VolumeField(required=False, placeholder="Optional, e.g. 5 gallons")
    intended_use = forms.CharField(max_length=30, label="Intended use",
                                   widget=forms.TextInput(attrs={'placeholder': 'e.g. Primary, Aging'}))
    last_passivation = forms.DateField(required=False, label="Last passivation",
                                       widget=DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    serial = forms.CharField(max_length=20, required=False, label="Serial / tag")
    toast_level = forms.CharField(max_length=25, required=False, label="Toast level")

    def __init__(self, *args, vessel: Vessel | None = None, **kwargs):
        self.vessel = vessel
        if vessel is not None:
            initial = {
                'name': vessel.name, 'capacity': vessel.capacity, 'fill': vessel.fill,
                'intended_use': vessel.intended_use,
            }
            fermenter = vessel.fermenter_set.first()
            barrel = vessel.barrel_set.first()
            if fermenter:
                initial['last_passivation'] = fermenter.last_passivation
            if barrel:
                initial.update(serial=barrel.serial, toast_level=barrel.toastLevel)
            kwargs.setdefault('initial', initial)
        super().__init__(*args, **kwargs)
        if vessel is not None:
            del self.fields['vessel_type']  # set once, on Add

    @property
    def chosen_type(self) -> str:
        """The vessel's type: fixed on Edit, the submitted choice on Add."""
        return self.vessel.vessel_type if self.vessel is not None else self.cleaned_data.get('vessel_type', '')

    def clean_name(self) -> str:
        name = self.cleaned_data['name'].strip()
        problem = vessel_name_problem(name, exclude_pk=self.vessel.pk if self.vessel else None)
        if problem:
            raise ValidationError(problem)
        return name

    def clean(self):
        cleaned = super().clean()
        if self.chosen_type == Vessel.TYPE_BARREL:
            for field in ('serial', 'toast_level'):
                if not cleaned.get(field) and field not in self.errors:
                    self.add_error(field, "Required for a barrel.")
        capacity, fill = cleaned.get('capacity'), cleaned.get('fill')
        if capacity is not None and fill is not None and fill.to(capacity.units) > capacity:
            self.add_error('fill', f"Fill can't be more than the capacity ({capacity}).")
        return cleaned


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
    recipe_notes = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), required=False)

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

    fermentable_id = forms.ModelChoiceField(
        queryset=Fermentable.objects.all(), label="Fermentable", widget=forms.HiddenInput()
    )
    is_fermentable = forms.BooleanField(label="Is Fermentable?", initial=True,
                                        required=False)

class AdjunctForm(RecipeFormItem):

    adjunct_id = forms.ModelChoiceField(
        queryset=Adjunct.objects.all(), label="Adjunct", widget=forms.HiddenInput()
    )
    time_to_add = forms.CharField(widget=forms.TextInput(attrs={'placeholder':'Time in days/hours/mins from start'}), label="Time to Add", required=True)

class YeastForm(forms.Form):
    yeast_id = forms.ModelChoiceField(
        queryset=Yeast.objects.all(), label="Yeast", widget=forms.HiddenInput()
    )
    amount = forms.CharField(widget=forms.TextInput,
                                     label = "Amount",
                                     required=True)
    notes = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), required=False)

class RecipeAddForm(forms.Form):
    class Meta:
        model = Recipe

    file = forms.FileField(widget=forms.FileInput(), label="Recipe File", required=False)
    name = forms.CharField(widget=forms.TextInput(attrs={'placeholder':'Recipe Name'}),required=True,label="Recipe Name")
    dateCreated = forms.DateField(label="Created Date",widget=NumberInput(attrs={'type':'date'}),required=True, initial=timezone.localdate)
    dateUpdated = forms.DateField(label="Last Updated",widget=NumberInput(attrs={'type':'date'}), required=False)
    version = forms.IntegerField(label="Version", required=False)
    style = forms.ModelChoiceField(queryset=BatchStyle.objects.all())
    category = forms.ModelChoiceField(queryset=BatchCategory.objects.all())
    brewer = forms.CharField(widget=forms.TextInput(attrs={'placeholder':'Name of brewer'}),label="Brewer", required=False)
    batchSize = forms.CharField(widget=forms.TextInput(attrs={'placeholder': 'Size of Batch'}), label = "Batch Size", required=True)
    notes = forms.CharField(widget=forms.Textarea(),label="Recipe Notes", required=False)
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