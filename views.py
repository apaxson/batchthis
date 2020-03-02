from django.shortcuts import render
from django.http import HttpResponse
from batchthis.models import Batch
# Create your views here.

def index(request):

    top_batches = Batch.objects.all()[:5]
    total_batch_count = Batch.objects.all().count()
    active_batch_count = Batch.objects.filter(active=True).count()
    active_fermenters = Fermenter
    return HttpResponse('Hello.  This is my first page.')


