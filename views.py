from django.shortcuts import render
from django.http import HttpResponse
from batchthis.models import Batch, Fermenter
# Create your views here.

def index(request):

    top_batches = Batch.objects.all()[:5]
    total_batch_count = Batch.objects.all().count()
    active_batch_count = Batch.objects.filter(active=True).count()
    active_fermenters = Fermenter.objects.filter(status=Fermenter.STATUS_ACTIVE)

    context = {
        'top_batches': top_batches,
        'total_batch_count': total_batch_count,
        'active_batch_count': active_batch_count,
        'active_fermenters': active_fermenters
    }
    return render(request,'index.html',context=context)


