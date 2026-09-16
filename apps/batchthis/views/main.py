import pdb

from django.shortcuts import render, reverse
from django.http import HttpResponseRedirect, HttpResponse, JsonResponse
from django.views.generic import FormView
from django.views.generic.detail import SingleObjectMixin
from django.contrib import messages
import logging
from apps.batchthis.models import Batch, Fermenter, BatchTestType, BatchNoteType, Vessel, Unit, Recipe, Fermentable, AdjunctUsage, RecipeYeasts,RecipeFermentable,RecipeAdjunct
from django.shortcuts import get_object_or_404
from apps.batchthis.forms import BatchTestForm, BatchNoteForm, BatchAdditionForm, RefractometerCorrectionForm, BatchAddForm, \
    BatchCategory
from apps.batchthis.forms import RecipeAddForm, FermentableForm, AdjunctForm, YeastForm
from django.forms.formsets import formset_factory
from apps.batchthis.lib.utils import Utils
from apps.batchthis.lib.faults import get_active_flags, get_rule_for, StagedFaultRule
from django.contrib.auth.decorators import login_required
from django.forms.models import model_to_dict, modelformset_factory
from pint import Quantity

logger = logging.getLogger(__name__)
# Create your views here.


# Presentation config for the instrument charts, keyed by BatchTestType.shortid
# (see apps/batchthis/migrations/0002_default_load.py for the full set). Color
# tokens are defined in cellar-ledger.css. Unit labels are informational only -
# BatchTest.units isn't normalized against these yet (see docs/flagged-notifications.md).
CHART_STYLE = {
    'specific-gravity': {'color': 'var(--honey-line)', 'decimals': 3, 'unit': ''},
    'ph': {'color': 'var(--must)', 'decimals': 2, 'unit': ''},
    'so2': {'color': 'var(--slate)', 'decimals': 0, 'unit': ' ppm'},
    'yan': {'color': 'var(--moss)', 'decimals': 0, 'unit': ' mg/L'},
    'ta': {'color': 'var(--plum)', 'decimals': 2, 'unit': ' g/L'},
    'temperature': {'color': 'var(--rust)', 'decimals': 1, 'unit': '°F'},
}
DEFAULT_CHART_STYLE = {'color': 'var(--ink-soft)', 'decimals': 2, 'unit': ''}


def _build_series(batch, shortid, rule=None):
    """
    Date/value series for one BatchTestType, shaped for the instrument charts.
    'rows' pairs them back up for the table fallback - Django templates can't
    zip two parallel lists on their own.

    If `rule` is a StagedFaultRule, 'bandMin'/'bandMax' are added: the same
    min/max curve rule.evaluate() checks against, sampled at each reading's
    own timestamp (elapsed hours since batch.startdate) rather than a fixed
    time grid, so the chart can't drift out of sync with what gets flagged.
    """
    tests = batch.tests.filter(type__shortid=shortid).order_by('datetime')
    series = {"shortid": shortid, "dates": [], "values": [], "rows": []}
    is_staged = isinstance(rule, StagedFaultRule) and batch.startdate is not None
    if is_staged:
        series["bandMin"] = []
        series["bandMax"] = []
    strfmt = "%m/%d/%y"
    for test in tests:
        date_str = test.datetime.strftime(strfmt)
        series["dates"].append(date_str)
        series["values"].append(test.value)
        series["rows"].append((date_str, test.value))
        if is_staged:
            elapsed_hours = (test.datetime - batch.startdate).total_seconds() / 3600
            band_min, band_max = rule.bounds_at(elapsed_hours)
            series["bandMin"].append(band_min)
            series["bandMax"].append(band_max)
    return series


def index(request):
    recent_batches = Batch.objects.all()[:5]
    total_batch_count = Batch.objects.all().count()
    active_batches = Batch.objects.filter(active=True)
    active_batch_count = len(active_batches)
    active_fermenters = Fermenter.objects.filter(vessel__status=Vessel.STATUS_ACTIVE)
    active_fermenters_count = len(active_fermenters)
    total_volume = 0
    fermenter_detail = {}
    for batch in active_batches:
        total_volume += batch.size
        fermenter_detail[batch.fermenter.vessel.name] = {'batch': batch.name, 'size': batch.size}

    flags = get_active_flags(active_batches)

    context = {
        'active_batches': active_batches,
        'active_fermenters': active_fermenters,
        'recent_batches': recent_batches,
        'total_batch_count': total_batch_count,
        'active_batch_count': active_batch_count,
        'active_fermenters_count': active_fermenters_count,
        'total_volume': total_volume,
        'fermenter_detail': fermenter_detail,
        'flags': flags,
    }
    return render(request, 'batchthis/index.html', context=context)


@login_required
def batchListing(request):
    batches = Batch.objects.all()

    context = {
        'batches': batches
    }
    return render(request, 'batchthis/batches.html', context=context)


def batch(request, pk):
    if request.method == "GET":
        batch = get_object_or_404(Batch, pk=pk)
        testTypes = BatchTestType.objects.all()
        fermenters = batch.fermenter
        recipe = batch.recipe
        gravity_tests = batch.tests.filter(type__shortid='specific-gravity')
        percent_complete = batch.percent_complete()
        thirdSugarBreak = round(batch.startingGravity - ((batch.startingGravity - batch.estimatedEndGravity) / 3), 3).magnitude
        ferm_notes = batch.notes.filter(notetype__name='Fermentation Note')
        gen_notes = batch.notes.filter(notetype__name='General Note')
        taste_notes = batch.notes.filter(notetype__name='Tasting Note')

        ph_rule = get_rule_for('ph')
        so2_rule = get_rule_for('so2')

        gravityChart = _build_series(batch, 'specific-gravity')
        phChart = _build_series(batch, 'ph', rule=ph_rule)
        so2Chart = _build_series(batch, 'so2')

        current_gravity_value = batch.current_gravity()
        estABV = round(Utils.potentialABV(startSG=batch.startingGravity.magnitude, endSG=current_gravity_value)[0], 1)

        context = {
            "batch": batch,
            "percentComplete": percent_complete,
            "gravityChart": gravityChart,
            "phChart": phChart,
            "so2Chart": so2Chart,
            "currentGravity": current_gravity_value,
            "currentPh": phChart["values"][-1] if phChart["values"] else None,
            "currentSo2": so2Chart["values"][-1] if so2Chart["values"] else None,
            "estABV": estABV,
            "so2Threshold": so2_rule.minimum if so2_rule else None,
            "gravityTests": gravity_tests,
            "testTypes": testTypes,
            "fermenters": fermenters,
            "thirdSugarBreak": thirdSugarBreak,
            "startingGravity": batch.startingGravity,
            "endingGravity": batch.estimatedEndGravity,
            "gennotes": gen_notes,
            "fermnotes": ferm_notes,
            "tastenotes": taste_notes,
            "recipe": recipe,
            "flags": get_active_flags([batch]),
        }
        return render(request, 'batchthis/batch.html', context=context)


def recipeListing(request):
    recipes = Recipe.objects.all()
    context = {'recipes': recipes}
    return render(request,'batchthis/recipes.html', context=context)

def recipe(request, pk):
    if request.method == "GET":
        recipe = Recipe.objects.get_object_or_404(Recipe, pk=pk)

    pass

def addRecipe(request, pk=None):
    if request.method == "GET":
        form = RecipeAddForm()
        fermentables = adjuncts = yeasts = recipe = None
        if pk:
            # We have a recipe to edit.  Load it up
            recipe = Recipe.objects.get(pk=pk)
            form = RecipeAddForm(initial=model_to_dict(recipe))
            form.fields['style'].initial = recipe.category.style.pk #Form was not setting style.  Let's manually set it
            fermentables = recipe.fermentables.all()
            adjuncts = recipe.adjuncts.all()
            yeasts = recipe.yeasts.all()
        context = {'form': form, 'recipe': recipe, 'fermentables': fermentables, 'adjuncts': adjuncts, 'yeasts': yeasts}
        return render(request, 'batchthis/addRecipe2.html', context)
    else:
        form = RecipeAddForm(request.POST)
        logger.debug("POST id: " + str(pk))
        if form.is_valid():
            recipe = Recipe()
            if pk:
                recipe = Recipe.objects.get(pk=pk)
            data = form.cleaned_data
            logger.debug(data)
            recipe.name = data['name']
            recipe.dateCreated = data['dateCreated']
            recipe.brewer = data['brewer']
            recipe.batchSize = data['batchSize']
            recipe.notes = data['notes']
            recipe.estOG = Quantity(data['estOG'], 'sg')
            recipe.estFG = Quantity(data['estFG'], 'sg')
            recipe.estABV = data['estABV']
            category = data['category']
            recipe.category = category
            recipe.save()
            logger.debug("recipe save() id: " + str(pk))
            return HttpResponseRedirect(reverse('editRecipe', kwargs={'pk': recipe.pk}))
        else:
            return render(request, 'batchthis/addRecipe2.html', {'form': form})


def editFermentables(request, pk=None):
    if request.method == "GET":
        if pk:
            recipe = Recipe.objects.get(pk=pk)
            if (len(recipe.fermentables.all()) > 0):
                fermentable_set = formset_factory(FermentableForm, extra=0, can_delete=True, can_delete_extra=True)
                fermentable_set = fermentable_set(initial=recipe.fermentables.all().values())
            else:
                fermentable_set = formset_factory(FermentableForm, extra=1)
        context = {'recipe': recipe, 'fermentable_set': fermentable_set}
        return render(request, 'batchthis/editFermentables.html', context)
    else:
        formset = formset_factory(FermentableForm, can_delete=True)
        fermentable_set = formset(request.POST)
        print("form data: " + str(request.POST))
        recipe = Recipe.objects.get(pk=pk)
        if fermentable_set.is_valid():
            fermentables = []
            for form_item in fermentable_set:
                if not form_item.cleaned_data.get('DELETE'):
                    # Don't include items marked for deletion
                    recipeFermentable = RecipeFermentable()
                    recipeFermentable.amount = form_item.cleaned_data.get('amount')
                    fermentable = form_item.cleaned_data.get('fermentable_id')
                    recipeFermentable.fermentable = fermentable
                    adjunctUsage = form_item.cleaned_data.get('intended_use_id')
                    recipeFermentable.intended_use = adjunctUsage
                    recipeFermentable.is_fermentable = form_item.cleaned_data.get('is_fermentable')
                    recipeFermentable.save()
                    fermentables.append(recipeFermentable)
            recipe.fermentables.set(fermentables)


            return HttpResponseRedirect(reverse('recipe', kwargs={'pk': recipe.pk}))
        else:
            print("not valid form")
            return render(request, 'batchthis/editFermentables.html', {'fermentable_set': fermentable_set})


def editAdjuncts(request, pk=None):
    if request.method == "GET":
        if pk:
            recipe = Recipe.objects.get(pk=pk)
            if len(recipe.adjuncts.all()) > 0:
                adjunct_set_form = formset_factory(AdjunctForm, extra=0, can_delete=True, can_delete_extra=True)
                adjunct_set = adjunct_set_form(initial=recipe.adjuncts.all().values())
            else:
                adjunct_set = formset_factory(AdjunctForm, extra=1)
        context = {'recipe': recipe, 'adjunct_set': adjunct_set}
        return render(request, 'batchthis/editAdjuncts.html', context)
    else:
        formset = formset_factory(AdjunctForm, can_delete=True)
        adjunct_set = formset(request.POST)
        recipe = Recipe.objects.get(pk=pk)
        if adjunct_set.is_valid():
            adjuncts = []
            for form_item in adjunct_set:
                if not form_item.cleaned_data.get('DELETE'):
                    # Don't include deleted items
                    recipeAdjunct = RecipeAdjunct()
                    recipeAdjunct.amount = form_item.cleaned_data.get('amount')
                    recipeAdjunct.adjunct = form_item.cleaned_data.get('adjunct_id')
                    recipeAdjunct.time_to_add = form_item.cleaned_data.get('time_to_add')
                    recipeAdjunct.notes = form_item.cleaned_data.get('recipe_notes')
                    recipeAdjunct.intended_use = form_item.cleaned_data.get('intended_use_id')
                    recipeAdjunct.save()
                    adjuncts.append(recipeAdjunct)
            recipe.adjuncts.set(adjuncts)
            return HttpResponseRedirect(reverse('recipe', kwargs={'pk': recipe.pk}))
        else:
            return render(request, 'batchthis/editAdjuncts.html', {'adjunct_set': adjunct_set})


def editYeasts(request, pk=None):
    if request.method == "GET":
        if pk:
            recipe=Recipe.objects.get(pk=pk)
            if len(recipe.yeasts.all()) > 0:
                yeast_set_form = formset_factory(YeastForm, extra=0, can_delete=True, can_delete_extra=True)
                yeast_set = yeast_set_form(initial=recipe.yeasts.all().values())
            else:
                yeast_set = formset_factory(YeastForm, extra=1)
        context = {'recipe': recipe, 'yeast_set': yeast_set}
        return render(request, 'batchthis/editYeasts.html', context)
    else:
        formset = formset_factory(YeastForm, can_delete=True)
        yeast_set = formset(request.POST)
        recipe = Recipe.objects.get(pk=pk)
        if yeast_set.is_valid():
            yeasts = []
            for form_item in yeast_set:
                if not form_item.cleaned_data.get('DELETE'):
                    # Don't include deleted items
                    recipeYeast = RecipeYeasts()
                    recipeYeast.amount = form_item.cleaned_data.get('amount')
                    recipeYeast.yeast = form_item.cleaned_data.get('yeast_id')
                    recipeYeast.notes = form_item.cleaned_data.get('notes')
                    recipeYeast.save()
                    yeasts.append(recipeYeast)
            recipe.yeasts.set(yeasts)
            return HttpResponseRedirect(reverse(viewname='recipe', kwargs={'pk': recipe.pk}))
        else:
            return render(request, template_name='batchthis/editYeasts.html', context={'yeast_set': yeast_set})


def addBatch(request, pk=None):
    if request.method == "POST":
        form = BatchAddForm(request.POST)
        if form.is_valid():
            batch = Batch()
            batch.name = form.cleaned_data['name']
            batch.startdate = form.cleaned_data['startdate']
            batch.size = form.cleaned_data['size']
            batch.startingGravity = form.cleaned_data['startingGravity']
            batch.estimatedEndGravity = form.cleaned_data['estimatedEndGravity']
            batch.fermenter = form.cleaned_data['fermenter']
            newbatch = batch.save()
            batch.recipe = form.cleaned_data['recipe']
            batch.save()
            return HttpResponseRedirect(reverse('batch', kwargs={'pk': batch.pk}))
        else:
            return render(request, template_name='batchthis/addBatch.html', context={'form': form})
    else:
        if pk:
            batch = Batch.objects.get(pk=pk)
            form = BatchAddForm(initial=model_to_dict(batch))
            #form = BatchForm(batch)
            #form.fermenter = batch.fermenter
            #form.startdate = batch.startdate
            #pdb.set_trace()
        else:
            #form = BatchAddForm()
            form = BatchAddForm()
            form.fields['fermenter'].queryset = Fermenter.objects.filter(vessel__status=Vessel.STATUS_READY)
        return render(request, "batchthis/addBatch.html", {'form': form})


def batchTest(request, pk=None):
    if request.method == 'GET':
        if pk:
            form = BatchTestForm()
            form.fields['batch'].queryset = Batch.objects.filter(pk=pk)
            form.initial = {'batch': pk}
            # We have a batchID.  Let's auto assign the batch to the note
        else:
            form = BatchTestForm()
            # We don't have a batchID.  Only show active batches
            form.fields['batch'].queryset = Batch.objects.filter(active=True)
    else:
        form = BatchTestForm(request.POST)
        form.save()
        return HttpResponseRedirect(reverse('batch', kwargs={'pk': pk}))
    return render(request, "batchthis/addTest.html", {'form': form})


def batchAddition(request, pk=None):
    if request.method == 'GET':
        form = BatchAdditionForm()
        if pk:
            form.fields['batch'].queryset = Batch.objects.filter(pk=pk)
            form.initial = {'batch': pk}
        else:
            form.fields['batch'].queryset = Batch.objects.filter(active=True)
    else:
        form = BatchAdditionForm(request.POST)
        form.save()
        return HttpResponseRedirect(reverse('batch', kwargs={'pk': pk}))
    return render(request, "batchthis/addAddon.html", {'form': form})


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
        return HttpResponseRedirect(reverse('batch', kwargs={'pk': pk}))
    return render(request, "batchthis/addNote.html", {'form': form})


def activity(request, pk=None):
    batch = Batch.objects.get(pk=pk)
    activity = batch.activity.all().order_by('datetime')
    context = {
        'activity': activity
    }
    return render(request, "batchthis/activity.html", context=context)


def refractometerCorrection(request):
    form = RefractometerCorrectionForm(initial={'startUnit': 'bx', 'currentUnit': 'bx'})
    result = (0, 0)
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
        'sg': '%.3f' % result[0],  # Format SG to normal readable notation
        'abv': round(result[1], 1)
    }
    return render(request, 'batchthis/util.refractometer.html', context)


def batchGraphs(request, pk):
    # One instrument panel per test type this batch actually has readings for -
    # no need to show a chart for a test that was never performed.
    batch = Batch.objects.get(pk=pk)

    testGroup = {}
    for test_type in BatchTestType.objects.all():
        series = _build_series(batch, test_type.shortid)
        if not series['values']:
            continue
        series['name'] = test_type.name
        series.update(CHART_STYLE.get(test_type.shortid, DEFAULT_CHART_STYLE))
        testGroup[test_type.name] = series

    context = {"batch": batch, "tests": testGroup}
    return render(request, "batchthis/batchGraphs.html", context)


def categoryFilterByStyle(request):
    style_id = request.GET.get('style')
    categories = BatchCategory.objects.filter(style__id=style_id)
    return render(request, 'batchthis/rpc/categoryFilterByStyle.html', {'categories':categories})

def parseRecipeFile(request):
    file = request.FILES['file']
    data = None
    return HttpResponse(data,content_type="application/json")

def getDataFromRecipe(request):
    recipe_id = request.GET.get('recipe')
    recipe = Recipe.objects.get(pk=recipe_id)
    orig_grav = recipe.estOG.magnitude
    final_grav = recipe.estFG.magnitude
    name = recipe.name
    data = {'est_og': orig_grav, 'est_fg': final_grav, 'name': name}
    return JsonResponse(data, status=200)