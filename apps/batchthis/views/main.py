import pdb

from django.shortcuts import render, reverse
from django.http import HttpResponseRedirect, HttpResponse, JsonResponse
from django.views.generic import FormView
from django.views.generic.detail import SingleObjectMixin
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
import logging
from django.utils import timezone
from django.db.models import Prefetch
from apps.batchthis.models import Batch, BatchStage, BatchStageEvent, Fermenter, BatchTestType, BatchNoteType, Vessel, Unit, Recipe, Fermentable, AdjunctUsage, RecipeYeasts,RecipeFermentable,RecipeAdjunct
from django.shortcuts import get_object_or_404
from apps.batchthis.forms import BatchTestForm, BatchNoteForm, BatchAdditionForm, RefractometerCorrectionForm, BatchAddForm, BatchEditForm, \
    BatchCategory
from apps.batchthis.forms import RecipeAddForm, FermentableForm, AdjunctForm, YeastForm, BatchStageForm, BatchTransferForm, VesselForm
from django.forms.formsets import formset_factory
from apps.batchthis.lib.utils import Utils
from apps.batchthis.services import (
    set_vessel_status, take_vessel_out_of_service, return_vessel_to_service, status_before_out_of_service,
    transition_stage_event, transfer_batch, create_vessel, update_vessel,
)
from apps.batchthis.lib.faults import get_active_flags, get_rule_for, StagedFaultRule
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
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
        series["values"].append(test.chart_value)
        series["rows"].append((date_str, test.chart_value))
        if is_staged:
            elapsed_hours = (test.datetime - batch.startdate).total_seconds() / 3600
            band_min, band_max = rule.bounds_at(elapsed_hours)
            series["bandMin"].append(band_min)
            series["bandMax"].append(band_max)
    return series


def index(request):
    recent_batches = Batch.objects.all()[:5]
    total_batch_count = Batch.objects.all().count()
    # select_related covers batch.current_vessel for both the view and the template.
    active_batches = Batch.objects.filter(active=True).select_related('vessel', 'fermenter__vessel')
    active_batch_count = len(active_batches)
    total_volume = 0
    vessels_in_use = []
    for batch in active_batches:
        total_volume += batch.size
        vessel = batch.current_vessel
        if vessel.status == Vessel.STATUS_OUT:
            # A vessel holding a batch can't be taken out of service through the
            # app, so this means the data was changed some other way.
            logger.error("index: active batch %s '%s' is in out-of-service vessel %s '%s'",
                         batch.pk, batch.name, vessel.pk, vessel.name)
            continue
        vessels_in_use.append({'vessel': vessel, 'batch': batch})
    vessels_in_use_count = len({row['vessel'].pk for row in vessels_in_use})
    logger.debug("index: %d active batches in %d vessels", active_batch_count, vessels_in_use_count)

    flags = get_active_flags(active_batches)

    context = {
        'active_batches': active_batches,
        'recent_batches': recent_batches,
        'total_batch_count': total_batch_count,
        'active_batch_count': active_batch_count,
        'vessels_in_use_count': vessels_in_use_count,
        'total_volume': total_volume,
        'vessels_in_use': vessels_in_use,
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
        # Prefetched stage events feed both timeline durations with one query.
        batch = get_object_or_404(
            Batch.objects.select_related('vessel', 'fermenter__vessel').prefetch_related(
                Prefetch('stage_events', queryset=BatchStageEvent.objects.select_related('stage', 'vessel'))
            ),
            pk=pk,
        )
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

        vessel_stays = batch.vessel_durations()
        full_aging = batch.full_aging()
        stage_events = list(batch.stage_events.all())
        # Transfer-only events (no stage) don't change the batch's stage.
        last_event = next((e for e in reversed(stage_events) if e.stage is not None), None)
        logger.debug("batch: pk=%s %d stage events, %d vessel stays, full_aging=%s",
                     pk, len(stage_events), len(vessel_stays), full_aging)

        timeline = batch.timeline_bar()

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
            # Stage timeline (TODO-BatchStage.txt step 6)
            "current_state": last_event.stage.to_state if last_event else None,
            "completed_event": last_event if last_event and last_event.stage.to_state == BatchStage.STATE_COMPLETED else None,
            "vessel_stays": vessel_stays,
            "current_stay": vessel_stays[-1] if vessel_stays and vessel_stays[-1].is_open else None,
            "full_aging": full_aging,
            "timeline": timeline,
            # One grid column per timeline segment, sized by its time; bands span their segments.
            "timeline_columns": " ".join(f"minmax(9rem, {seg.weight:.1f}fr)" for seg in timeline.segments),
        }
        return render(request, 'batchthis/batch.html', context=context)


def recipeListing(request):
    recipes = Recipe.objects.all()
    context = {'recipes': recipes}
    return render(request,'batchthis/recipes.html', context=context)

def recipe(request, pk):
    recipe = get_object_or_404(Recipe, pk=pk)
    context = {
        'recipe': recipe,
        'fermentables': recipe.fermentables.all(),
        'adjuncts': recipe.adjuncts.all(),
        'yeasts': recipe.yeasts.all(),
        'batches': recipe.batch_set.order_by('-startdate'),
    }
    return render(request, 'batchthis/recipe.html', context=context)

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
                # A brand-new recipe has no fermentables yet - one blank row to start.
                fermentable_set = formset_factory(FermentableForm, extra=1, can_delete=True, can_delete_extra=True)
                fermentable_set = fermentable_set()
        context = {'recipe': recipe, 'fermentable_set': fermentable_set}
        return render(request, 'batchthis/editFermentables.html', context)
    else:
        formset = formset_factory(FermentableForm, can_delete=True)
        fermentable_set = formset(request.POST)
        logger.debug("editFermentables POST for recipe_id=%s: %s", pk, request.POST)
        recipe = Recipe.objects.get(pk=pk)
        if fermentable_set.is_valid():
            fermentables = []
            for form_item in fermentable_set:
                if not form_item.cleaned_data:
                    # A blank extra row (e.g. added via "Add fermentable" then left
                    # empty) - Django's formset marks it empty_permitted and skips
                    # validation for it, so there's nothing here to save.
                    continue
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
            logger.debug("editFermentables: invalid formset for recipe_id=%s: %s", pk, fermentable_set.errors)
            return render(request, 'batchthis/editFermentables.html', {'fermentable_set': fermentable_set, 'recipe': recipe})


def editAdjuncts(request, pk=None):
    if request.method == "GET":
        if pk:
            recipe = Recipe.objects.get(pk=pk)
            if len(recipe.adjuncts.all()) > 0:
                adjunct_set_form = formset_factory(AdjunctForm, extra=0, can_delete=True, can_delete_extra=True)
                adjunct_set = adjunct_set_form(initial=recipe.adjuncts.all().values())
            else:
                # A brand-new recipe has no adjuncts yet - one blank row to start.
                adjunct_set_form = formset_factory(AdjunctForm, extra=1, can_delete=True, can_delete_extra=True)
                adjunct_set = adjunct_set_form()
        context = {'recipe': recipe, 'adjunct_set': adjunct_set}
        return render(request, 'batchthis/editAdjuncts.html', context)
    else:
        formset = formset_factory(AdjunctForm, can_delete=True)
        adjunct_set = formset(request.POST)
        recipe = Recipe.objects.get(pk=pk)
        if adjunct_set.is_valid():
            adjuncts = []
            for form_item in adjunct_set:
                if not form_item.cleaned_data:
                    # A blank extra row (e.g. added via "Add adjunct" then left
                    # empty) - Django's formset marks it empty_permitted and skips
                    # validation for it, so there's nothing here to save.
                    continue
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
            logger.debug("editAdjuncts: invalid formset for recipe_id=%s: %s", pk, adjunct_set.errors)
            return render(request, 'batchthis/editAdjuncts.html', {'adjunct_set': adjunct_set, 'recipe': recipe})


def editYeasts(request, pk=None):
    if request.method == "GET":
        if pk:
            recipe=Recipe.objects.get(pk=pk)
            if len(recipe.yeasts.all()) > 0:
                yeast_set_form = formset_factory(YeastForm, extra=0, can_delete=True, can_delete_extra=True)
                yeast_set = yeast_set_form(initial=recipe.yeasts.all().values())
            else:
                # A brand-new recipe has no yeasts yet - one blank row to start.
                yeast_set_form = formset_factory(YeastForm, extra=1, can_delete=True, can_delete_extra=True)
                yeast_set = yeast_set_form()
        context = {'recipe': recipe, 'yeast_set': yeast_set}
        return render(request, 'batchthis/editYeasts.html', context)
    else:
        formset = formset_factory(YeastForm, can_delete=True)
        yeast_set = formset(request.POST)
        recipe = Recipe.objects.get(pk=pk)
        if yeast_set.is_valid():
            yeasts = []
            for form_item in yeast_set:
                if not form_item.cleaned_data:
                    # A blank extra row (e.g. added via "Add yeast" then left
                    # empty) - Django's formset marks it empty_permitted and skips
                    # validation for it, so there's nothing here to save.
                    continue
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
            logger.debug("editYeasts: invalid formset for recipe_id=%s: %s", pk, yeast_set.errors)
            return render(request, template_name='batchthis/editYeasts.html', context={'yeast_set': yeast_set, 'recipe': recipe})


def addBatch(request):
    if request.method == "POST":
        form = BatchAddForm(request.POST)
        if form.is_valid():
            batch = Batch()
            batch.name = form.cleaned_data['name']
            batch.startdate = form.cleaned_data['startdate']
            batch.size = form.cleaned_data['size']
            batch.startingGravity = Quantity(float(form.cleaned_data['startingGravity']), 'sg')
            batch.estimatedEndGravity = Quantity(float(form.cleaned_data['estimatedEndGravity']), 'sg')
            batch.fermenter = form.cleaned_data['fermenter']
            batch.vessel = batch.fermenter.vessel
            try:
                # Batch and vessel status commit together, or neither does.
                with transaction.atomic():
                    batch.save()
                    batch.recipe = form.cleaned_data['recipe']
                    batch.save()
                    set_vessel_status(batch.fermenter.vessel, Vessel.STATUS_ACTIVE, batch=batch, notes="Batch created")
            except Exception:
                logger.exception("addBatch: failed to create batch %r on fermenter %s", batch.name, batch.fermenter)
                form.add_error(None, "Couldn't create the batch. Nothing was saved - please try again.")
                return render(request, template_name='batchthis/addBatch.html', context={'form': form})
            logger.info("addBatch: created batch %s '%s' in vessel '%s'", batch.pk, batch.name, batch.fermenter.vessel.name)
            return HttpResponseRedirect(reverse('batch', kwargs={'pk': batch.pk}))
        else:
            logger.debug("addBatch: invalid form: %s", form.errors)
            return render(request, template_name='batchthis/addBatch.html', context={'form': form})
    else:
        form = BatchAddForm()
        return render(request, "batchthis/addBatch.html", {'form': form})


@login_required
def editBatch(request, pk):
    """
    Correct an existing batch in place. Vessel and start date are shown
    read-only - see BatchEditForm for why.
    """
    batch = get_object_or_404(Batch.objects.select_related('fermenter__vessel', 'vessel', 'recipe'), pk=pk)
    initial = {
        'name': batch.name,
        'recipe': batch.recipe_id,
        'size': str(batch.size),
        # Plain strings in the same format the fields post back, so has_changed() is accurate.
        'startingGravity': f"{batch.startingGravity.magnitude:.3f}",
        'estimatedEndGravity': f"{batch.estimatedEndGravity.magnitude:.3f}",
    }
    if request.method == "POST":
        form = BatchEditForm(request.POST, initial=initial)
        if form.is_valid():
            if not form.has_changed():
                logger.debug("editBatch: batch %s '%s' submitted with no changes", pk, batch.name)
                return HttpResponseRedirect(reverse('batch', kwargs={'pk': pk}))
            batch.name = form.cleaned_data['name']
            batch.recipe = form.cleaned_data['recipe']
            batch.size = form.cleaned_data['size']
            batch.startingGravity = Quantity(form.cleaned_data['startingGravity'], 'sg')
            batch.estimatedEndGravity = Quantity(form.cleaned_data['estimatedEndGravity'], 'sg')
            try:
                # Only the edited fields: never rewrites vessel/fermenter/startdate/active.
                batch.save(update_fields=['name', 'recipe', 'size', 'startingGravity', 'estimatedEndGravity'])
            except Exception:
                logger.exception("editBatch: failed to save batch %s '%s'", pk, batch.name)
                form.add_error(None, "Couldn't save the batch. Nothing was changed - please try again.")
                return render(request, 'batchthis/editBatch.html', {'form': form, 'batch': batch})
            logger.info("editBatch: updated batch %s '%s' (%s)", pk, batch.name, ", ".join(form.changed_data))
            return HttpResponseRedirect(reverse('batch', kwargs={'pk': pk}))
        logger.debug("editBatch: invalid form for batch %s: %s", pk, form.errors)
    else:
        form = BatchEditForm(initial=initial)
    return render(request, 'batchthis/editBatch.html', {'form': form, 'batch': batch})


def _now_minute():
    return timezone.localtime().replace(second=0, microsecond=0)


def _is_modal_request(request) -> bool:
    """True when cellar-ledger.js's shared form modal (#formModal) is asking."""
    return request.headers.get('x-requested-with') == 'XMLHttpRequest'


def _save_batch_record(request, batch: Batch, form, template: str, what: str, partial: str | None = None):
    """
    Shared POST/GET handling for Add test / Add note / Add addon: validate, attach
    to the batch from the URL (never from the form), save, redirect to the batch.
    Invalid input re-shows the form with its errors; nothing is saved.
    With `partial` and a modal request, GET/invalid POST return just the form
    (the partial template) and a successful POST returns {"saved": true} - the
    modal then reloads the batch page.
    """
    modal = partial is not None and _is_modal_request(request)
    if request.method == 'POST':
        if form.is_valid():
            record = form.save(commit=False)
            record.batch = batch
            try:
                record.save()
            except Exception:
                logger.exception("%s: failed to save for batch %s", what, batch.pk)
                form.add_error(None, f"Couldn't save the {what}. Nothing was saved - please try again.")
            else:
                logger.info("%s: saved %s on batch %s '%s'", what, record.pk, batch.pk, batch.name)
                if modal:
                    return JsonResponse({'saved': True})
                return HttpResponseRedirect(reverse('batch', kwargs={'pk': batch.pk}))
        logger.debug("%s: invalid form for batch %s: %s", what, batch.pk, form.errors.as_data())
    return render(request, partial if modal else template, {'form': form, 'batch': batch, 'modal': modal})


@login_required
def batchTest(request, pk):
    batch = get_object_or_404(Batch, pk=pk)
    form = BatchTestForm(request.POST or None, initial={'datetime': _now_minute()}, batch=batch)
    return _save_batch_record(request, batch, form, "batchthis/addTest.html", "test")


@login_required
def batchAddition(request, pk):
    batch = get_object_or_404(Batch, pk=pk)
    form = BatchAdditionForm(request.POST or None)
    return _save_batch_record(request, batch, form, "batchthis/addAddon.html", "addition",
                              partial="batchthis/includes/_addon_form.html")


@login_required
def batchNote(request, pk, noteType=None):
    batch = get_object_or_404(Batch, pk=pk)
    initial = {'date': _now_minute()}
    if noteType:
        note_type = BatchNoteType.objects.filter(name=noteType).first()
        if note_type:
            initial['notetype'] = note_type.pk
        else:
            logger.warning("batchNote: unknown note type %r in URL for batch %s; showing all types", noteType, pk)
    form = BatchNoteForm(request.POST or None, initial=initial)
    return _save_batch_record(request, batch, form, "batchthis/addNote.html", "note")


@login_required
def batchStage(request, pk):
    """Log the batch's next workflow stage (Pitch, Racking, a Filtering, Complete Batch)."""
    batch = get_object_or_404(Batch.objects.select_related('vessel', 'fermenter__vessel'), pk=pk)
    if request.method == 'POST':
        form = BatchStageForm(request.POST, batch=batch)
        if form.is_valid():
            data = form.cleaned_data
            try:
                transition_stage_event(
                    batch, data['stage'],
                    timestamp=data['timestamp'], dst_vessel=data['dst_vessel'], notes=data['notes'],
                )
            except ValidationError as e:
                # Workflow rule from the service (e.g. timestamp bounds); nothing was saved.
                form.add_error(None, e.messages)
            except Exception:
                logger.exception("batchStage: failed to log %r for batch %s", data['stage'], pk)
                form.add_error(None, "Couldn't log the stage. Nothing was saved - please try again.")
            else:
                return HttpResponseRedirect(reverse('batch', kwargs={'pk': pk}))
        logger.debug("batchStage: batch %s form errors: %s", pk, form.errors.as_data())
    else:
        form = BatchStageForm(batch=batch, initial={'timestamp': timezone.localtime().replace(second=0, microsecond=0)})

    context = {
        'batch': batch,
        'form': form,
        'current_state': batch.current_state,
        # Lets the page show the destination picker only for stages that move the batch.
        'transfer_stage_ids': ",".join(
            str(pk) for pk in BatchStage.objects.filter(transfers_batch=True).order_by('pk').values_list('pk', flat=True)
        ),
    }
    return render(request, "batchthis/addBatchStage.html", context)


@login_required
def batchTransfer(request, pk):
    """Ad-hoc transfer outside the workflow - e.g. to empty a vessel that has to go Out of Service."""
    batch = get_object_or_404(Batch.objects.select_related('vessel', 'fermenter__vessel'), pk=pk)
    if request.method == 'POST':
        form = BatchTransferForm(request.POST, batch=batch)
        if not batch.active:
            form.add_error(None, f"Batch '{batch.name}' is complete and can't be transferred.")
        elif form.is_valid():
            data = form.cleaned_data
            try:
                transfer_batch(
                    batch, data['dst_vessel'],
                    reason=data['reason'], stage=data['stage'], timestamp=data['timestamp'],
                )
            except ValidationError as e:
                form.add_error(None, e.messages)
            except Exception:
                logger.exception("batchTransfer: failed for batch %s", pk)
                form.add_error(None, "Couldn't transfer the batch. Nothing was saved - please try again.")
            else:
                return HttpResponseRedirect(reverse('batch', kwargs={'pk': pk}))
        logger.debug("batchTransfer: batch %s form errors: %s", pk, form.errors.as_data())
    else:
        form = BatchTransferForm(batch=batch, initial={'timestamp': timezone.localtime().replace(second=0, microsecond=0)})
    return render(request, "batchthis/transferBatch.html", {'batch': batch, 'form': form})


def activity(request, pk=None):
    batch = get_object_or_404(Batch, pk=pk)
    activity = batch.activity.all().order_by('-datetime')
    context = {
        'batch': batch,
        'activity': activity
    }
    return render(request, "batchthis/activity.html", context=context)


# Status artwork in static/batchthis/img/, per vessel type. Barrels have no art yet.
VESSEL_ICON_PREFIX = {'Fermenter': 'wine-tank', 'Aging Tank': 'aging-tank'}
VESSEL_ICON_SUFFIX = {
    Vessel.STATUS_READY: 'ready',
    Vessel.STATUS_ACTIVE: 'active',
    Vessel.STATUS_DIRTY: 'dirty',
    Vessel.STATUS_OUT: 'out-of-service',
}


def _vessel_status_icon(vessel: Vessel) -> str | None:
    prefix = VESSEL_ICON_PREFIX.get(vessel.vessel_type)
    suffix = VESSEL_ICON_SUFFIX.get(vessel.status)
    if not (prefix and suffix):
        return None
    return f'batchthis/img/{prefix}-{suffix}.svg'


@login_required
def vesselListing(request):
    return render(request, 'batchthis/vessels.html')


def _vessel_form_details(data: dict) -> dict:
    return {key: data.get(key) for key in ('name', 'capacity', 'fill', 'intended_use', 'last_passivation')} | {
        'serial': data.get('serial') or '', 'toast_level': data.get('toast_level') or '',
    }


@login_required
def vesselCreate(request):
    """Add a vessel (Fermenter / Aging Tank / Barrel); it starts Clean/Ready."""
    form = VesselForm(request.POST or None)
    if request.method == 'POST':
        if form.is_valid():
            try:
                vessel = create_vessel(vessel_type=form.cleaned_data['vessel_type'], **_vessel_form_details(form.cleaned_data))
            except ValidationError as e:
                form.add_error(None, e.messages)
            except Exception:
                logger.exception("vesselCreate: failed to add vessel %r", form.cleaned_data.get('name'))
                form.add_error(None, "Couldn't add the vessel. Nothing was saved - please try again.")
            else:
                return HttpResponseRedirect(reverse('vessel', kwargs={'pk': vessel.pk}))
        logger.debug("vesselCreate: form errors: %s", form.errors.as_data())
    return render(request, 'batchthis/vesselForm.html', {'form': form, 'vessel': None})


@login_required
def vesselEdit(request, pk):
    """Edit a vessel's details. Type and status can't be changed here."""
    vessel = get_object_or_404(Vessel, pk=pk)
    form = VesselForm(request.POST or None, vessel=vessel)
    if request.method == 'POST':
        if form.is_valid():
            try:
                update_vessel(vessel, **_vessel_form_details(form.cleaned_data))
            except ValidationError as e:
                form.add_error(None, e.messages)
            except Exception:
                logger.exception("vesselEdit: failed to update vessel %s", pk)
                form.add_error(None, "Couldn't save the vessel. Nothing was changed - please try again.")
            else:
                return HttpResponseRedirect(reverse('vessel', kwargs={'pk': pk}))
        logger.debug("vesselEdit: vessel %s form errors: %s", pk, form.errors.as_data())
    return render(request, 'batchthis/vesselForm.html', {'form': form, 'vessel': vessel})


@login_required
def vessel(request, pk):
    vessel = get_object_or_404(Vessel, pk=pk)
    # Newest first for display; Meta.ordering on VesselStatusEvent is oldest-first.
    history = vessel.status_events.select_related('batch').order_by('-timestamp')
    current_event = history.first()
    context = {
        'vessel': vessel,
        'current_batch': vessel.current_batch,
        'history': history,
        'status_since': current_event.timestamp if current_event else None,
        'status_icon': _vessel_status_icon(vessel),
        'can_mark_cleaned': vessel.status == Vessel.STATUS_DIRTY,
        'can_take_out_of_service': vessel.status in (Vessel.STATUS_READY, Vessel.STATUS_DIRTY),
        'is_out_of_service': vessel.status == Vessel.STATUS_OUT,
        'out_of_service_reason': current_event.notes if current_event and vessel.status == Vessel.STATUS_OUT else '',
        'return_to_status': status_before_out_of_service(vessel) if vessel.status == Vessel.STATUS_OUT else '',
    }
    logger.debug("vessel: pk=%s '%s' status=%r, %d history events", pk, vessel.name, vessel.status, len(history))
    return render(request, 'batchthis/vessel.html', context=context)


@login_required
@require_POST
def markVesselCleaned(request, pk):
    """
    The one manual transition in the normal cycle: Needs Cleaning -> Clean/Ready.
    Cleaning is physical, so nothing else moves a vessel back to ready.
    """
    vessel = get_object_or_404(Vessel, pk=pk)
    logger.debug("markVesselCleaned: pk=%s '%s' status=%r", pk, vessel.name, vessel.status)
    if vessel.status != Vessel.STATUS_DIRTY:
        logger.error("markVesselCleaned: rejected for vessel %s '%s' in status %r", pk, vessel.name, vessel.status)
        messages.error(request, f"Only a vessel that needs cleaning can be marked cleaned - "
                                f"'{vessel.name}' is {vessel.status}.")
        return HttpResponseRedirect(reverse('vessel', kwargs={'pk': pk}))
    notes = request.POST.get('notes', '').strip()[:250] or "Cleaned"
    try:
        set_vessel_status(vessel, Vessel.STATUS_READY, notes=notes)
    except Exception:
        logger.exception("markVesselCleaned: failed for vessel %s '%s'", pk, vessel.name)
        messages.error(request, f"Couldn't mark '{vessel.name}' cleaned. Nothing was changed - please try again.")
        return HttpResponseRedirect(reverse('vessel', kwargs={'pk': pk}))
    logger.info("markVesselCleaned: vessel %s '%s' is Clean/Ready", pk, vessel.name)
    return HttpResponseRedirect(reverse('vessel', kwargs={'pk': pk}))


@login_required
@require_POST
def takeVesselOutOfService(request, pk):
    """Clean/Ready or Needs Cleaning -> Out of Service, with a required reason."""
    vessel = get_object_or_404(Vessel, pk=pk)
    logger.debug("takeVesselOutOfService: pk=%s '%s' status=%r", pk, vessel.name, vessel.status)
    try:
        take_vessel_out_of_service(vessel, request.POST.get('reason', ''))
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    except Exception:
        logger.exception("takeVesselOutOfService: failed for vessel %s '%s'", pk, vessel.name)
        messages.error(request, f"Couldn't take '{vessel.name}' out of service. Nothing was changed - please try again.")
    return HttpResponseRedirect(reverse('vessel', kwargs={'pk': pk}))


@login_required
@require_POST
def returnVesselToService(request, pk):
    """Out of Service -> the status the vessel had before it was taken out."""
    vessel = get_object_or_404(Vessel, pk=pk)
    logger.debug("returnVesselToService: pk=%s '%s' status=%r", pk, vessel.name, vessel.status)
    try:
        return_vessel_to_service(vessel, request.POST.get('notes', ''))
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    except Exception:
        logger.exception("returnVesselToService: failed for vessel %s '%s'", pk, vessel.name)
        messages.error(request, f"Couldn't return '{vessel.name}' to service. Nothing was changed - please try again.")
    return HttpResponseRedirect(reverse('vessel', kwargs={'pk': pk}))


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