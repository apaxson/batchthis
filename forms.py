from django.forms import ModelForm
from .models import BatchTest, BatchNote

class BatchTestForm(ModelForm):
    class Meta:
        model = BatchTest
        fields = "__all__"

class BatchNoteForm(ModelForm):
    class Meta:
        model = BatchNote
        fields = "__all__"
