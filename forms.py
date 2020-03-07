from django.forms import ModelForm
from .models import BatchTest

class BatchTestForm(ModelForm):
    class Meta:
        model = BatchTest
        fields = "__all__"
