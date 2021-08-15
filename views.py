from django.shortcuts import render, reverse
from django.http import HttpResponse, HttpResponseRedirect
from batchthis.models import Batch, Fermenter, BatchTestType, BatchNoteType
from django.shortcuts import get_object_or_404
from .forms import BatchTestForm, BatchNoteForm, BatchAdditionForm, RefractometerCorrectionForm
from batchthis.utils import Utils


# Create your views here.

def index(request):

    top_batches = Batch.objects.all()[:5]
    total_batch_count = Batch.objects.all().count()
    active_batches = Batch.objects.filter(active=True)
    active_batch_count = len(active_batches)
    active_fermenters = Fermenter.objects.filter(status=Fermenter.STATUS_ACTIVE)
    active_fermenters_count = len(active_fermenters)
    total_volume = 0
    for batch in active_batches:
        total_volume += batch.size

    fermenter_detail = {}
    for fermenter in active_fermenters:
        fermenter_batch = fermenter.batch.filter(active=True)
        if not fermenter.name in fermenter_detail.keys():
            fermenter_detail[fermenter.name] = {'batch': fermenter_batch[0].name, 'size': fermenter_batch[0].size}
    context = {
        'active_batches': active_batches,
        'active_fermenters': active_fermenters,
        'top_batches': top_batches,
        'total_batch_count': total_batch_count,
        'active_batch_count': active_batch_count,
        'active_fermenters_count': active_fermenters_count,
        'total_volume': total_volume,
        'fermenter_detail': fermenter_detail
    }
    return render(request,'index.html',context=context)

def batch(request, pk):

    batch = get_object_or_404(Batch,pk=pk)
    testTypes = BatchTestType.objects.all()
    fermenters = batch.fermenter.all()
    gravity_tests = batch.tests.filter(type__shortid='specific-gravity')
    current_gravity = batch.startingGravity
    if len(gravity_tests) > 0:
        # We have gravity tests.  Get the latest
        current_gravity = gravity_tests[len(gravity_tests)-1].value
    percent_complete = round((batch.startingGravity-current_gravity)/(batch.startingGravity-batch.estimatedEndGravity)*100)
    thirdSugarBreak = round(batch.startingGravity-((batch.startingGravity-batch.estimatedEndGravity)/3),3)
    thirdSugarBreakPercent = round((batch.startingGravity-thirdSugarBreak)/(batch.startingGravity-batch.estimatedEndGravity)*100)
    ferm_notes = batch.notes.filter(notetype__name='Fermentation Note')
    gen_notes = batch.notes.filter(notetype__name='General Note')
    taste_notes = batch.notes.filter(notetype__name='Tasting Note')

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
        "thirdSugarBreakPercent": thirdSugarBreakPercent,
        "startingGravity": batch.startingGravity,
        "endingGravity": batch.estimatedEndGravity,
        "gennotes": gen_notes,
        "fermnotes": ferm_notes,
        "tastenotes": taste_notes
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

def batchAddition(request, pk=None):
    if request.method == 'GET':
        form = BatchAdditionForm()
        if pk:
            form.fields['batch'].queryset = Batch.objects.filter(pk=pk)
            form.initial = {'batch':pk}
        else:
            form.fields['batch'].queryset = Batch.objects.filter(active=True)
    else:
        form = BatchAdditionForm(request.POST)
        form.save()
        return HttpResponseRedirect(reverse('batch', kwargs={'pk': pk}))
    return render(request, "addAddon.html", {'form': form})


def batchNote(request, pk=None, noteType=None):
    if request.method == 'GET':
        form = BatchNoteForm()
        form.initial = {}
        if pk:
            form.fields['batch'].queryset = Batch.objects.filter(pk=pk)
            form.initial['batch'] = pk
        if noteType:
            noteTypes = BatchNoteType.objects.filter(name=noteType)
            form.fields['notetype'].queryset = noteTypes
            form.initial['notetype'] = noteTypes[0].pk
        else:
            form.fields['batch'].queryset = Batch.objects.all()
    else:
        form = BatchNoteForm(request.POST)
        form.save()
        return HttpResponseRedirect(reverse('batch', kwargs={'pk':pk}))
    return render(request, "addNote.html", {'form':form})

def activity(request, pk=None):
    print("Getting activity for batch " + str(pk))
    batch = Batch.objects.get(pk=pk)
    activity = batch.activity.all().order_by('datetime')
    print("Found " + str(len(activity)) + " entries in activity")
    context = {
        'activity': activity
    }
    return render(request, "activity.html", context=context)

def refractometerCorrection(request):
    form = RefractometerCorrectionForm()
    result = (0,0)
    if request.method == "POST":
        form = RefractometerCorrectionForm(request.POST)
        params = {}
        if form.is_valid():
            # Calculate Correction
            startData = form.cleaned_data['startData']
            startUnit = form.cleaned_data['startUnit']
            currentData = form.cleaned_data['currentData']
            currentUnit = form.cleaned_data['currentUnit']
            if startUnit == 'sg':
                params['startSG'] = startData
            else:
                params['startBrix'] = startData
            if currentUnit == 'sg':
                params['currentSG'] = currentData
            else:
                params['currentBrix'] = currentData
            result = Utils.refractometerCorrection(**params)

    context = {
        'form': form,
        'sg': '%.3f' % result[0], # Format SG to normal readable notation
        'abv': round(result[1],1)
    }
    return render(request, 'util.refractometer.html', context)

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

    context = {"tests": testGroup,
               "testTypes": testGroup.keys()
               }
    return render(request, "batchGraphs.html", context)