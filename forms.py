from django.forms import ModelForm
from .models import BatchTest, BatchNote, BatchAddition

class BatchTestForm(ModelForm):
    class Meta:
        model = BatchTest
        fields = "__all__"

class BatchNoteForm(ModelForm):
    class Meta:
        model = BatchNote
        fields = "__all__"

class BatchAdditionForm(ModelForm):
    class Meta:
        model = BatchAddition
        fields = "__all__"