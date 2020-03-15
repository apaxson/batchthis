from django.shortcuts import render, reverse
from django.http import HttpResponse, HttpResponseRedirect
from batchthis.models import Batch, Fermenter, BatchTestType
from django.shortcuts import get_object_or_404
from .forms import BatchTestForm


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

def batch(request, pk):

    batch = get_object_or_404(Batch,pk=pk)
    testTypes = BatchTestType.objects.all()
    fermenters = batch.fermenter.all()
    gravity_tests = batch.tests.filter(type__shortid='specific-gravity')
    current_gravity = gravity_tests[len(gravity_tests)-1].value
    percent_complete = round((batch.startingGravity-current_gravity)/(batch.startingGravity-batch.estimatedEndGravity)*100)
    thirdSugarBreak = round(batch.startingGravity-((batch.startingGravity-batch.estimatedEndGravity)/3),3)
    thirdSugarBreakPercent = round((batch.startingGravity-thirdSugarBreak)/(batch.startingGravity-batch.estimatedEndGravity)*100)

    gravityChart = {}
    for test in gravity_tests:
        if not "dates" in gravityChart.keys():
            gravityChart["shortid"] = "specific-gravity"
            gravityChart["dates"] = []
            gravityChart["values"] = []
        strfmt = "%m/%d/%y"
        gravityChart["dates"].append(test.datetime.strftime(strfmt))
        gravityChart["values"].append(test.value)

    context = {
        "batch": batch,
        "percentComplete": percent_complete,
        "gravityChart": gravityChart,
        "gravityTests": gravity_tests,
        "testTypes": testTypes,
        "fermenters": fermenters,
        "thirdSugarBreak": thirdSugarBreak,
        "thirdSugarBreakPercent": thirdSugarBreakPercent
    }
    return render(request, 'batch.html', context=context)

def batchTest(request, pk=None):
    if request.method == 'GET':
        if pk:
            form = BatchTestForm()
            form.fields['batch'].queryset = Batch.objects.filter(pk=pk)
            form.initial = {'batch':pk}
            # We have a batchID.  Let's auto assign the batch to the note
        else:
            form = BatchTestForm()
            # We don't have a batchID.  Only show active batches
            form.fields['batch'].queryset = Batch.objects.filter(active=True)
    else:
        form = BatchTestForm(request.POST)
        form.save()
        return HttpResponseRedirect(reverse('batch', kwargs={'pk': pk}))
    return render(request,"addTest.html", {'form':form})

#TODO Add Note Form

def batchGraphs(request, pk):
    # Capture each type of test.  No need to show graphs on tests not performed
    batch = Batch.objects.get(pk=pk)
    tests = batch.tests.all()

    # Build data var for chart data
    testGroup = {}
    for test in tests:
        if not test.type.name in testGroup.keys():
            testGroup[test.type.name] = {}
            testGroup[test.type.name]['shortid'] = test.type.shortid
            testGroup[test.type.name]['dates'] = []
            testGroup[test.type.name]['values'] = []

        date_format = "%m/%d/%y"
        strdate = test.datetime.strftime(date_format)
        testGroup[test.type.name]['dates'].append(strdate)
        testGroup[test.type.name]['values'].append(test.value)

    print(testGroup)
    context = {"tests": testGroup,
               "testTypes": testGroup.keys()
               }
    return render(request, "batchGraphs.html", context)