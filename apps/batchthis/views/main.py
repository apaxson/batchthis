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
from django.db.models import Count, Prefetch
from django.utils.safestring import mark_safe
import json
from apps.batchthis.models import PlanStep, WorkflowTemplate
from apps.batchthis.models import Batch, BatchStage, BatchStageEvent, Fermenter, BatchTestType, BatchNoteType, Vessel, Unit, Recipe, Fermentable, AdjunctUsage, RecipeYeasts,RecipeFermentable,RecipeAdjunct
from django.shortcuts import get_object_or_404
from apps.batchthis.forms import BatchTestForm, BatchNoteForm, BatchAdditionForm, RefractometerCorrectionForm, BatchAddForm, BatchEditForm, \
    BatchCategory
from apps.batchthis.forms import RecipeAddForm, FermentableForm, AdjunctForm, YeastForm, BatchStageForm, BatchTransferForm, VesselForm
from apps.batchthis.forms import WorkflowTemplateForm, PlanStepFormSet, RecipePlanForm
from django.forms.formsets import formset_factory
from apps.batchthis.lib.utils import Utils
from apps.batchthis.services import (
    set_vessel_status, take_vessel_out_of_service, return_vessel_to_service, status_before_out_of_service,
    OUT_OF_SERVICE_FROM,
    transition_stage_event, transfer_batch, create_vessel, update_vessel,
    plan_totals, save_workflow_template, delete_workflow_template, save_recipe_plan, clear_recipe_plan,
    copy_plan_to_batch, save_batch_plan, batch_plan_locked_reason,
    plan_progress, allowed_next_stages, save_batch_edit,
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
        if vessel is None:
            continue   # packaged (Bottles / Kegs) - no longer in a vessel
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

        # The plan's upcoming steps draw the time bar's future (step 11c); no plan -> placeholders.
        progress = plan_progress(batch)
        upcoming = [row.step for row in progress if row.status == 'upcoming'] if progress else None
        timeline = batch.timeline_bar(planned_steps=upcoming)
        plan = _plan_summary(batch.plan_steps.select_related('stage'))
        plan_locked = batch_plan_locked_reason(batch)

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
            # The batch's own plan (step 10): editable until Pitch; plan vs actual after (11e).
            "plan": plan,
            "plan_vs_actual": _plan_vs_actual(batch, progress) if progress and batch.current_state else None,
            "plan_editable": plan_locked is None,
            # One grid column per timeline segment, sized by its time; bands span their segments.
            "timeline_columns": " ".join(f"minmax(9rem, {seg.weight:.1f}fr)" for seg in timeline.segments),
        }
        return render(request, 'batchthis/batch.html', context=context)


def recipeListing(request):
    recipes = Recipe.objects.all()
    context = {'recipes': recipes}
    return render(request,'batchthis/recipes.html', context=context)

def _plan_summary(steps) -> dict:
    """A plan's steps + planned time for includes/plan_steps.html (only timed steps count)."""
    steps = list(steps)
    totals = plan_totals(steps)
    return {
        'steps': steps,
        'total': _days_label(totals.total_days) if totals.total_days else None,
        'by_state': [(state, _days_label(totals.by_state[state]))
                     for state in BatchStage.TIMELINE_STATES if state in totals.by_state],
    }


PROGRESS_STATUS = {   # plan_progress() status -> (label, badge class)
    'done': ("Done", "cl-stage--complete"),
    'current': ("Current", "cl-stage--active"),
    'skipped': ("Skipped", "cl-stage--skipped"),
    'upcoming': ("Upcoming", "cl-stage--upcoming"),
    'unplanned': ("Unplanned", "cl-stage--unplanned"),
}


def _difference_label(days: float | None) -> str:
    if days is None:
        return "—"
    if days == 0:
        return "On plan"
    return ("+" if days > 0 else "−") + _days_label(abs(days))


def _plan_vs_actual(batch, progress) -> dict:
    """plan_progress() rows formatted for includes/plan_progress.html, plus the footer totals."""
    rows = []
    for row in progress:
        label, badge = PROGRESS_STATUS[row.status]
        actual_time = "—" if row.actual_days is None else _days_label(row.actual_days)
        if row.status == 'current' and row.actual_days is not None:
            actual_time += " so far"
        rows.append({
            'number': row.step.sort_order if row.step else "—",
            'stage': row.stage, 'status': label, 'badge': badge,
            'planned_vessel': row.step.vessel_type_hint if row.step else "—",
            'actual_vessel': row.actual_vessel or "—",
            'planned_day': "—" if row.planned_day is None else f"Day {row.planned_day:g}",
            'actual_day': "—" if row.actual_day is None else f"Day {row.actual_day:g}",
            'planned_time': "—" if row.planned_days is None else _days_label(row.planned_days),
            'actual_time': actual_time,
            # A step still in progress hasn't finished early - show the time left until it runs over.
            'difference': (f"{_days_label(-row.difference_days)} left"
                           if row.status == 'current' and row.difference_days is not None and row.difference_days < 0
                           else _difference_label(row.difference_days)),
            'notes': row.event.notes if row.event else (row.step.notes if row.step else ""),
        })
    planned_total = plan_totals(batch.plan_steps.all()).total_days
    pitched = next((row.event.timestamp for row in progress if row.event is not None), None)
    end = batch.enddate if not batch.active and batch.enddate else timezone.now()
    actual = None
    if pitched:
        actual = _days_label(round((end - pitched).total_seconds() / 86400, 1)) + ("" if not batch.active else " so far")
    return {'rows': rows, 'planned_total': _days_label(planned_total) if planned_total else None, 'actual': actual}


def recipe(request, pk):
    recipe = get_object_or_404(Recipe.objects.select_related('workflow_template'), pk=pk)
    context = {
        'recipe': recipe,
        'plan': _plan_summary(recipe.plan_steps.select_related('stage')),
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
        recipe = get_object_or_404(Recipe, pk=pk) if pk else None
        if form.is_valid():
            recipe = recipe or Recipe()
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
            logger.info("addRecipe: saved recipe %s '%s'", recipe.pk, recipe.name)
            return HttpResponseRedirect(reverse('recipe', kwargs={'pk': recipe.pk}))
        else:
            logger.debug("addRecipe: recipe %s form errors: %s", pk, form.errors.as_data())
            # Pass the recipe back so an edit with errors stays "Edit recipe" with its Back link.
            return render(request, 'batchthis/addRecipe2.html', {'form': form, 'recipe': recipe})


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


def _add_batch_context(form) -> dict:
    return {'form': form, 'recipes_with_plans': ",".join(str(pk) for pk in BatchAddForm.recipes_with_plans())}


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
                    copy_plan_to_batch(batch, template=form.cleaned_data['workflow_template'])
            except ValidationError as e:
                # The form already checks for a plan; this covers a race (e.g. plan cleared meanwhile).
                logger.debug("addBatch: plan copy rejected for %r: %s", batch.name, e.messages)
                form.add_error('workflow_template', e.messages)
                return render(request, template_name='batchthis/addBatch.html', context=_add_batch_context(form))
            except Exception:
                logger.exception("addBatch: failed to create batch %r on fermenter %s", batch.name, batch.fermenter)
                form.add_error(None, "Couldn't create the batch. Nothing was saved - please try again.")
                return render(request, template_name='batchthis/addBatch.html', context=_add_batch_context(form))
            logger.info("addBatch: created batch %s '%s' in vessel '%s'", batch.pk, batch.name, batch.fermenter.vessel.name)
            return HttpResponseRedirect(reverse('batch', kwargs={'pk': batch.pk}))
        else:
            logger.debug("addBatch: invalid form: %s", form.errors)
            return render(request, template_name='batchthis/addBatch.html', context=_add_batch_context(form))
    else:
        form = BatchAddForm()
        return render(request, "batchthis/addBatch.html", _add_batch_context(form))


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
            try:
                # Saves only the edited fields, and logs what changed in the same transaction.
                save_batch_edit(
                    batch,
                    name=form.cleaned_data['name'],
                    recipe=form.cleaned_data['recipe'],
                    size=form.cleaned_data['size'],
                    starting_gravity=form.cleaned_data['startingGravity'],
                    estimated_end_gravity=form.cleaned_data['estimatedEndGravity'],
                )
            except Exception:
                logger.exception("editBatch: failed to save batch %s '%s'", pk, batch.name)
                batch.refresh_from_db()   # drop the unsaved values so the page shows what's stored
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
    return _save_batch_record(request, batch, form, "batchthis/addTest.html", "test",
                              partial="batchthis/includes/_test_form.html")


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
    return _save_batch_record(request, batch, form, "batchthis/addNote.html", "note",
                              partial="batchthis/includes/_note_form.html")


def _into_label(step) -> str:
    """A planned step's vessel type mid-sentence: "any Aging Tank", "Bottles", "same vessel"."""
    label = step.vessel_type_hint
    return label if step.vessel_type in dict(PlanStep.PACKAGING_CHOICES) else label[0].lower() + label[1:]


def _plan_hint(step) -> str:
    """ "Planned next: Racking into any Aging Tank (~30 days planned)" for the Stage transition page."""
    hint = f"Planned next: {step.stage.name}"
    if step.vessel_type != PlanStep.VESSEL_CURRENT:
        hint += f" into {_into_label(step)}"
    if step.has_duration:
        hint += f" (~{_days_label(step.planned_days)} planned)"
    return hint


def _stage_plans(batch) -> tuple[dict, "BatchPlanStep | None"]:
    """
    For the Stage transition page: per allowed next stage, the next upcoming planned step with
    that stage (its vessel type, packaging and hint), plus the step to suggest - the
    first upcoming planned step the workflow allows now. Empty / None without a plan.
    """
    allowed = {stage.pk for stage in allowed_next_stages(batch)}
    upcoming = [row.step for row in plan_progress(batch) if row.status == 'upcoming' and row.step.stage_id in allowed]
    plans = {}
    for step in upcoming:
        if str(step.stage_id) in plans:
            continue
        packaging = step.vessel_type if step.vessel_type in dict(PlanStep.PACKAGING_CHOICES) else ""
        plans[str(step.stage_id)] = {
            'vessel_type': step.vessel_type if step.vessel_type in dict(Vessel.TYPE_CHOICES) else "",
            'vessel_label': _into_label(step),
            'packaging': packaging,
            'hint': _plan_hint(step),
        }
    next_step = upcoming[0] if upcoming else None
    logger.debug("_stage_plans: batch %s next=%s plans=%s", batch.pk, next_step and next_step.stage.shortid, plans)
    return plans, next_step


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
                    timestamp=data['timestamp'], dst_vessel=data['dst_vessel'], packaging=data['packaging'],
                    notes=data['notes'],
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
    stage_plans, next_step = _stage_plans(batch)
    if request.method != 'POST':
        initial = {'timestamp': timezone.localtime().replace(second=0, microsecond=0)}
        if next_step is not None:   # suggest the plan's next step (step 11d)
            initial['stage'] = next_step.stage.pk
            if next_step.vessel_type in dict(PlanStep.PACKAGING_CHOICES):
                initial['packaging'] = next_step.vessel_type
        form = BatchStageForm(batch=batch, initial=initial)

    context = {
        'batch': batch,
        'form': form,
        'current_state': batch.current_state,
        'next_step': next_step,
        'next_step_hint': _plan_hint(next_step) if next_step else '',
        'stage_plans': mark_safe(json.dumps(stage_plans)),   # stage pks and fixed labels only
        'packaging_stage_ids': ",".join(
            str(pk) for pk in BatchStage.objects.filter(to_state=BatchStage.STATE_BOTTLING).values_list('pk', flat=True)
        ),
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
VESSEL_ICON_PREFIX = {Vessel.TYPE_FERMENTER: 'wine-tank', Vessel.TYPE_AGING_TANK: 'aging-tank'}
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
        'can_take_out_of_service': vessel.status in OUT_OF_SERVICE_FROM,
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


OUT_OF_SERVICE_FORM_PARTIAL = 'batchthis/includes/_out_of_service_form.html'


@login_required
def takeVesselOutOfServiceForm(request, pk):
    """
    The reason form for Take out of service. The vessel page's actions menu opens it
    in the shared #formModal (just the partial); without JavaScript it's a full page.
    It posts to takeVesselOutOfService.
    """
    vessel = get_object_or_404(Vessel, pk=pk)
    modal = _is_modal_request(request)
    logger.debug("takeVesselOutOfServiceForm: pk=%s '%s' status=%r modal=%s", pk, vessel.name, vessel.status, modal)
    if vessel.status not in OUT_OF_SERVICE_FROM:
        logger.error("takeVesselOutOfServiceForm: vessel %s '%s' is %r; can't go out of service",
                     pk, vessel.name, vessel.status)
        messages.error(request, f"'{vessel.name}' is {vessel.status} - only a {Vessel.STATUS_READY} or "
                                f"{Vessel.STATUS_DIRTY} vessel can be taken out of service.")
        return HttpResponseRedirect(reverse('vessel', kwargs={'pk': pk}))
    context = {'vessel': vessel, 'modal': modal}
    return render(request, OUT_OF_SERVICE_FORM_PARTIAL if modal else 'batchthis/vesselOutOfService.html', context)


@login_required
@require_POST
def takeVesselOutOfService(request, pk):
    """
    Clean/Ready or Needs Cleaning -> Out of Service, with a required reason.
    From the form modal: {"saved": true} on success, or the form again with the error.
    """
    vessel = get_object_or_404(Vessel, pk=pk)
    modal = _is_modal_request(request)
    reason = request.POST.get('reason', '')
    logger.debug("takeVesselOutOfService: pk=%s '%s' status=%r modal=%s", pk, vessel.name, vessel.status, modal)
    error = None
    try:
        take_vessel_out_of_service(vessel, reason)
    except ValidationError as e:
        error = ' '.join(e.messages)
    except Exception:
        logger.exception("takeVesselOutOfService: failed for vessel %s '%s'", pk, vessel.name)
        error = f"Couldn't take '{vessel.name}' out of service. Nothing was changed - please try again."
    if modal:
        if error is None:
            return JsonResponse({'saved': True})
        return render(request, OUT_OF_SERVICE_FORM_PARTIAL,
                      {'vessel': vessel, 'modal': True, 'error': error, 'reason': reason.strip()})
    if error is not None:
        messages.error(request, error)
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


# ---------- Recipe plan (Edit plan) ----------

@login_required
def editRecipePlan(request, pk):
    """
    Edit a recipe's own plan. ?template=<pk> pre-fills the rows from a workflow
    (nothing is saved until Save, which records it as "Copied from"). A recipe
    with no plan starts from the default chain.
    """
    recipe = get_object_or_404(Recipe.objects.select_related('workflow_template'), pk=pk)
    if request.method == 'POST':
        form = RecipePlanForm(request.POST)
        formset = PlanStepFormSet(request.POST, prefix='steps')
        if form.is_valid() and formset.is_valid():
            try:
                save_recipe_plan(recipe, formset.plan_rows(), template=form.cleaned_data['workflow_template'])
            except ValidationError as e:
                form.add_error(None, e.messages)
            except Exception:
                logger.exception("editRecipePlan: failed to save the plan for recipe %s", pk)
                form.add_error(None, "Couldn't save the plan. Nothing was changed - please try again.")
            else:
                return HttpResponseRedirect(reverse('recipe', kwargs={'pk': pk}))
        logger.debug("editRecipePlan: recipe %s errors form=%s steps=%s non-form=%s", pk,
                     form.errors.as_data(), formset.errors, formset.non_form_errors())
        chosen = form.cleaned_data.get('workflow_template') if form.is_valid() else None
    else:
        chosen = None
        if request.GET.get('template'):
            chosen = get_object_or_404(WorkflowTemplate, pk=request.GET['template'])
            initial = _plan_step_initial(chosen.steps)
        elif recipe.plan_steps.exists():
            initial = _plan_step_initial(recipe.plan_steps)
        else:
            initial = _default_plan_initial()
        form = RecipePlanForm(initial={'workflow_template': chosen.pk if chosen else None})
        formset = PlanStepFormSet(initial=initial, prefix='steps')
    context = {
        'recipe': recipe, 'form': form, 'formset': formset, 'chosen': chosen,
        'templates': WorkflowTemplate.objects.all(), 'has_plan': recipe.plan_steps.exists(),
        'stage_rules': _stage_rules_json(),
    }
    return render(request, 'batchthis/recipePlan.html', context)


@login_required
@require_POST
def clearRecipePlan(request, pk):
    """A plan is optional: remove the recipe's steps and its "Copied from" reference."""
    recipe = get_object_or_404(Recipe, pk=pk)
    try:
        clear_recipe_plan(recipe)
    except Exception:
        logger.exception("clearRecipePlan: failed for recipe %s", pk)
        messages.error(request, "Couldn't clear the plan. Nothing was changed - please try again.")
        return HttpResponseRedirect(reverse('editRecipePlan', kwargs={'pk': pk}))
    return HttpResponseRedirect(reverse('recipe', kwargs={'pk': pk}))


# ---------- Batch plan (Edit plan, until Pitch) ----------

@login_required
def editBatchPlan(request, pk):
    """Edit a batch's own plan until Pitch; after that (or with no plan) it shows read-only."""
    batch = get_object_or_404(Batch.objects.select_related('workflow_template'), pk=pk)
    locked = batch_plan_locked_reason(batch)
    formset = None
    if request.method == 'POST' and not locked:
        formset = PlanStepFormSet(request.POST, prefix='steps')
        if formset.is_valid():
            try:
                save_batch_plan(batch, formset.plan_rows())
            except ValidationError as e:
                locked = ' '.join(e.messages)   # e.g. pitched meanwhile - show read-only with why
            except Exception:
                logger.exception("editBatchPlan: failed to save the plan for batch %s", pk)
                locked = "Couldn't save the plan. Nothing was changed - please try again."
            else:
                return HttpResponseRedirect(reverse('batch', kwargs={'pk': pk}))
        else:
            logger.debug("editBatchPlan: batch %s errors steps=%s non-form=%s",
                         pk, formset.errors, formset.non_form_errors())
    elif request.method == 'POST':
        logger.debug("editBatchPlan: batch %s rejected - %s", pk, locked)
    if formset is None and not locked:
        formset = PlanStepFormSet(initial=_plan_step_initial(batch.plan_steps), prefix='steps')
    context = {
        'batch': batch, 'formset': None if locked else formset, 'locked': locked,
        'plan': _plan_summary(batch.plan_steps.select_related('stage')), 'stage_rules': _stage_rules_json(),
    }
    return render(request, 'batchthis/batchPlan.html', context)


# ---------- Workflow templates (Settings > Workflows) ----------

def _days_label(days: float) -> str:
    days = round(days, 1)
    return f"{days:g} day" if days == 1 else f"{days:g} days"


@login_required
def workflowListing(request):
    templates = (WorkflowTemplate.objects
                 .annotate(step_count=Count('steps', distinct=True), recipe_count=Count('recipes', distinct=True))
                 .prefetch_related('steps__stage'))
    rows = []
    for template in templates:
        total = plan_totals(template.steps.all()).total_days
        rows.append({'template': template, 'planned': _days_label(total) if total else None})
    logger.debug("workflowListing: %d workflow template(s)", len(rows))
    return render(request, 'batchthis/workflows.html', {'rows': rows})


def _plan_step_initial(steps) -> list[dict]:
    """Saved plan steps (a template's or a recipe's) as PlanStepFormSet initial rows."""
    return [{'stage': s.stage, 'vessel_type': s.vessel_type, 'planned_duration': s.planned_duration, 'notes': s.notes}
            for s in steps.select_related('stage')]


def _default_plan_initial() -> list[dict]:
    """A new plan starts as the shortest valid chain; vessel types only where the stage fixes them."""
    stages = {s.shortid: s for s in BatchStage.objects.filter(
        shortid__in=['pitch', 'racking', 'sterile-filtering', 'complete-batch'])}
    return [{'stage': stages['pitch'], 'vessel_type': Vessel.TYPE_FERMENTER},
            {'stage': stages['racking']},
            {'stage': stages['sterile-filtering']},
            {'stage': stages['complete-batch'], 'vessel_type': PlanStep.VESSEL_CURRENT}]


def _stage_rules_json() -> str:
    return mark_safe(json.dumps(PlanStepFormSet.stage_rules()))  # stage pks and fixed labels only


def _workflow_form_page(request, template: WorkflowTemplate | None):
    """Add (template=None) or edit a workflow template and its steps - one page for both."""
    form = WorkflowTemplateForm(request.POST or None, template=template)
    if request.method == 'POST':
        formset = PlanStepFormSet(request.POST, prefix='steps')
        if form.is_valid() and formset.is_valid():
            try:
                template = save_workflow_template(template, name=form.cleaned_data['name'],
                                                  description=form.cleaned_data['description'],
                                                  rows=formset.plan_rows())
            except ValidationError as e:
                form.add_error(None, e.messages)
            except Exception:
                logger.exception("workflow form: failed to save template %s", template.pk if template else None)
                form.add_error(None, "Couldn't save the workflow. Nothing was changed - please try again.")
            else:
                messages.success(request, f"Saved workflow '{template.name}'.")
                return HttpResponseRedirect(reverse('workflowListing'))
        logger.debug("workflow form: errors form=%s steps=%s non-form=%s",
                     form.errors.as_data(), formset.errors, formset.non_form_errors())
    else:
        initial = _plan_step_initial(template.steps) if template is not None else _default_plan_initial()
        formset = PlanStepFormSet(initial=initial, prefix='steps')
    context = {'form': form, 'formset': formset, 'template': template, 'stage_rules': _stage_rules_json()}
    return render(request, 'batchthis/workflowForm.html', context)


@login_required
def workflowCreate(request):
    return _workflow_form_page(request, None)


@login_required
def workflowEdit(request, pk):
    return _workflow_form_page(request, get_object_or_404(WorkflowTemplate, pk=pk))


@login_required
def workflowDelete(request, pk):
    """GET asks for confirmation; POST deletes. Recipes that copied it keep their steps."""
    template = get_object_or_404(WorkflowTemplate, pk=pk)
    if request.method == 'POST':
        name = template.name
        try:
            delete_workflow_template(template)
        except Exception:
            logger.exception("workflowDelete: failed to delete template %s '%s'", pk, name)
            messages.error(request, f"Couldn't delete workflow '{name}'. Nothing was changed - please try again.")
            return HttpResponseRedirect(reverse('editWorkflow', kwargs={'pk': pk}))
        messages.success(request, f"Deleted workflow '{name}'.")
        return HttpResponseRedirect(reverse('workflowListing'))
    recipes = template.recipes.order_by('name')
    return render(request, 'batchthis/workflowDelete.html', {'template': template, 'recipes': recipes})


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