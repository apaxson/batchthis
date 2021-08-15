from django.forms import ModelForm
from django import forms
from .models import BatchTest, BatchNote, BatchAddition

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