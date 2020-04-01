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
        super().__init(*args,**kwargs)
        self.fields["date"].widget = DateTimeWidget()
        self.fields["date"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"]

class BatchAdditionForm(ModelForm):
    class Meta:
        model = BatchAddition
        fields = "__all__"