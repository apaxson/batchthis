import pdb

from django.core.files.uploadedfile import UploadedFile
from apps.batchthis.models import AdjunctType, AdjunctUsage, Adjunct, Fermentable, FermentableType, Yeast
from apps.batchthis.models import BatchStage, Recipe, BatchCategory, BatchStyle
from apps.batchthis.lib.bslib import Recipe as R
from apps.batchthis.lib.bslib import Fermentable as F
from apps.batchthis.lib.bslib import Adjunct as A
from apps.batchthis.lib.bslib import Yeast as Y
from django.contrib.auth.models import Group
from django.core.files.storage import FileSystemStorage
from django.shortcuts import render
from django.contrib import messages
import xmltodict
from pint import Quantity
from apps.batchthis.models import RecipeFermentable, RecipeYeasts, RecipeAdjunct
import logging

log = logging.getLogger(__name__)

BEERSMITH_VOLUME_FERMENTABLE_TYPES = ['juice','extract']
BEERSMITH_MASS_FERMENTABLE_TYPES = ['grain','honey','fruit','sugar']

def clean_xml(xml_data):
    xml_data.replace("&ldquo;", "").replace("&ndash;", "").replace("&rsquo;", "").replace("&AElig;", "").replace(
        "&rdquo;", "")
    return xml_data

def get_adjunct(data):
    """
    Get or Save Adjunct
    :return: QuerySet Adjunct()
             boolean created
    """
    pass

def get_adjunct_use(name):
    """
        Get or Save AdjunctUsage
        :return: QuerySet AdjunctUsage()
                 boolean created
    """
    return AdjunctUsage.objects.get_or_create(name=name)


def get_adjunct_type(name):
    """
        Get or Save AdjunctType
        :return: QuerySet AdjunctType()
                 boolean created
    """
    return AdjunctType.objects.get_or_create(name=name)


def get_fermentable_type(name):
    """
            Get or Save FermentableType
            :return: QuerySet FermentableType()
                     boolean created
        """
    return FermentableType.objects.get_or_create(name=name)


def admin_import(request):
    file_storage = FileSystemStorage()
    if request.method == "POST":
        if not request.FILES:
            messages.error(request,"No File Found")
            return render(request, 'batchthis/admin/import.html')
        if 'recipe_upload' in request.FILES.keys():
            """
                Parse BeerSmith recipe and add to database
            """

            log.info("Importing Recipe....")
            recipe_file = request.FILES['recipe_upload']
            filename = file_storage.save(recipe_file.name, recipe_file)
            recipe_file_url = file_storage.url(filename)
            recipe_file_path = file_storage.path(filename)
            recipe = R()
            recipe.parse(recipe_file_path)
            log.debug("Uploaded Recipe: " + recipe.name + " to " + recipe_file_path)
            obj_recipe = Recipe()
            if len(Recipe.objects.filter(name__iexact=recipe.name, version__exact=recipe.version)) == 1:
                # This is an existing Recipe.  Skip
                log.info("Recipe exists")
                messages.success(request, f"Recipe exists: '{recipe.name}'.....  Skipping") #TODO allow user to overwrite if desired
                return render(request, 'batchthis/admin/import.html')
            else:
                # Let's create a new entry
                style, style_created = BatchStyle.objects.get_or_create(name=recipe.styletype)
                bjcp_code = recipe.stylecatnum + recipe.styleletter
                category, cat_created = BatchCategory.objects.get_or_create(name=recipe.stylecat, style=style, bjcp_code=bjcp_code)
                obj_recipe.category = category
                obj_recipe.dateCreated = recipe.datecreated
                obj_recipe.name = recipe.name
                obj_recipe.bs_file = recipe_file
                obj_recipe.version = recipe.version
                obj_recipe.brewer = recipe.brewer
                obj_recipe.batchSize = Quantity(recipe.batchsize, "liters")
                obj_recipe.source = "BeerSmith v3 Upload"
                obj_recipe.estOG = Quantity(recipe.estog, "sg")
                obj_recipe.estFG = Quantity(recipe.estfg, "sg")
                obj_recipe.estABV = recipe.estabv
                obj_recipe.save()
                log.info("Saved recipe to DB: " + obj_recipe.name)
                # Create/Save fermentables/yeasts/adjuncts
                # FERMENTABLES
                fermentables = []
                rec_creates = {}
                rec_creates['fermentables'] = 0
                rec_creates['fermentable_types'] = 0
                for fermentable in recipe.fermentables:
                    # Check if Fermentable Exists
                    obj_fermentable = Fermentable()
                    if len(Fermentable.objects.filter(name__iexact=fermentable.name)) == 0: #TODO combine with version
                        # No Fermentable Found.  Let's create it
                        ferm_type, ferm_type_created = get_fermentable_type(fermentable.type)
                        if ferm_type_created:
                            rec_creates['fermentable_types'] += 1
                        obj_fermentable.name = fermentable.name
                        obj_fermentable.sugar_content = fermentable.brix
                        obj_fermentable.potential = fermentable.potential
                        obj_fermentable.color = fermentable.color
                        obj_fermentable.type = ferm_type
                        obj_fermentable.save()
                        rec_creates['fermentables'] += 1
                    else:
                        # Fermentable Exists.  GetFermentable to add to Recipe
                        obj_fermentable = Fermentable.objects.get(name__iexact=fermentable.name)
                    # We should have our fermentable now.  Let's add to a Recipe
                    recipe_fermentable = RecipeFermentable()
                    recipe_fermentable.fermentable = obj_fermentable
                    # Make sure amounts are a Quantity()
                    dimensionality = ''
                    if fermentable.type in BEERSMITH_VOLUME_FERMENTABLE_TYPES:
                        dimensionality = 'liters'
                    if fermentable.type in BEERSMITH_MASS_FERMENTABLE_TYPES:
                        dimensionality = 'kilograms'
                    recipe_fermentable.amount_metric = Quantity(fermentable.amount, dimensionality)
                    # The XML file does not include when to use the fermentable.  But, we MUST
                    # have "intended_use" in order to save.  Assume "Primary", though this may
                    # get us into trouble later, if we don't pay attention
                    recipe_fermentable.intended_use = AdjunctUsage.objects.get(name__iexact="Primary")
                    recipe_fermentable.save()
                    obj_recipe.fermentables.add(recipe_fermentable)
                    #TODO send user to addRecipe to modify recipe after saving.
                    # Some data, like is_fermentable and intended_use are not
                    # available in import file
                for adjunct in recipe.adjuncts:
                    rec_creates['adjuncts'] = 0
                    rec_creates['adjunct_types'] = 0
                    rec_creates['adjunct_use'] = 0
                    obj_adjunct = Adjunct()
                    # Check if Adjunct Exists
                    if len(Adjunct.objects.filter(name__iexact=adjunct.name)) == 0: #TODO Combine with version
                        # No Adjunct Exists.  Create it.
                        adj_type, adj_type_created = get_adjunct_type(adjunct.type)
                        if adj_type_created:
                            rec_creates['adjunct_types'] += 1
                        adj_use, adj_use_created = get_adjunct_use(adjunct.use)
                        if adj_use_created:
                            rec_creates['adjunct_use'] += 1
                        obj_adjunct.type = adj_type
                        obj_adjunct.use = adj_use
                        obj_adjunct.name = adjunct.name
                        obj_adjunct.use_for = adjunct.use_for
                        obj_adjunct.notes = adjunct.notes
                        obj_adjunct.ratio_amount = adjunct.ratio_amount
                        obj_adjunct.ratio_batch_size = adjunct.ratio_batch_size
                        obj_adjunct.save()
                        rec_creates['adjuncts'] += 1
                    else:
                        obj_adjunct = Adjunct.objects.get(name__iexact=adjunct.name)
                    recipe_adjunct = RecipeAdjunct()
                    recipe_adjunct.adjunct = obj_adjunct
                    dimensionality = ''
                    if adjunct.amount_is_weight:
                        dimensionality = 'kilograms'
                    else:
                        dimensionality = 'liters'
                    recipe_adjunct.amount = Quantity(adjunct.amount, dimensionality)
                    recipe_adjunct.time_to_add = Quantity(adjunct.display_time)
                    recipe_adjunct.amount_is_weight = adjunct.amount_is_weight
                    recipe_adjunct.recipe_notes = adjunct.notes
                    recipe_adjunct.intended_use = obj_adjunct.use
                    recipe_adjunct.save()
                    obj_recipe.adjuncts.add(recipe_adjunct)

                rec_creates["yeasts"] = 0
                for yeast in recipe.yeasts:
                    obj_yeast = Yeast()
                    # Check if Yeast exists
                    if len(Yeast.objects.filter(name__iexact=yeast.name)) == 0: #TODO combine with version
                        # No Yeast exists.  Let's create it.
                        obj_yeast.name = yeast.name
                        obj_yeast.version = yeast.version
                        obj_yeast.type = yeast.type
                        obj_yeast.form = yeast.form.lower()
                        obj_yeast.description = yeast.notes
                        obj_yeast.attenuation = yeast.attenuation
                        obj_yeast.min_temp = Quantity(yeast.min_temp, "degC")
                        obj_yeast.max_temp = Quantity(yeast.max_temp, "degC")
                        obj_yeast.flocculation = yeast.flocculation
                        obj_yeast.save()
                        rec_creates["yeasts"] += 1
                    else:
                        obj_yeast = Yeast.objects.get(name__iexact=yeast.name)
                    dimensionality = ''
                    if yeast.amount_is_weight:
                        dimensionality = "kilograms"
                    else:
                        dimensionality = "liters"
                    rec_yeast = RecipeYeasts()
                    rec_yeast.yeast = obj_yeast
                    rec_yeast.amount = Quantity(yeast.amount, dimensionality)
                    rec_yeast.save()
                    obj_recipe.yeasts.add(rec_yeast)

            #TODO Save Recipe
            messages.success(request, f"Successfully imported: {recipe.name}")
            return render(request, 'batchthis/admin/import.html', {'uploaded_file_url': recipe_file_url})

        elif 'misc_upload' in request.FILES.keys():
            """
                Parse Beersmith items to database to be used in Recipes
            """
            import_file = request.FILES['misc_upload']
            filename = file_storage.save(import_file.name, import_file)
            import_file_url = file_storage.url(filename)
            import_file_path = file_storage.path(filename)
            f = open(import_file_path, 'r')
            raw_data = f.read()
            clean_data = clean_xml(raw_data)
            f.close()
            parsed = xmltodict.parse(clean_data)
            counts = {}
            #Adjuncts
            if 'MISCS' in parsed.keys():
                counts['adjuncts'] = {'created': 0, 'skipped': 0}
                counts['adjunct_types'] = {'created': 0, 'skipped': 0}
                counts['adjunct_usage'] = {'created': 0, 'skipped': 0}
                adjunct_list = []
                for data in parsed['MISCS']['MISC']:
                    adjunct = A()
                    adjunct.load(data)
                    adjunct_list.append(adjunct)
                    #adj_type,created = AdjunctType.objects.get_or_create(name=adjunct.type)
                    adj_type,created = get_adjunct_type(adjunct.type)
                    if created:
                        counts['adjunct_types']['created'] += 1
                    else:
                        counts['adjunct_types']['skipped'] += 1
                    obj_adjunct = Adjunct()
                    obj_adjunct.type = adj_type
                    obj_adjunct.name = adjunct.name
                    obj_adjunct.description = adjunct.notes
                    adj_usage, created = get_adjunct_use(adjunct.use)
                    if created:
                        counts['adjunct_usage']['created'] += 1
                    else:
                        counts['adjunct_usage']['skipped'] += 1
                    obj_adjunct.use = adj_usage
                    obj_adjunct.ratio_amount = adjunct.amount
                    obj_adjunct.ratio_batch_size = adjunct.batchsize
                    if len(Adjunct.objects.filter(name__iexact=adjunct.name)) == 0:
                        obj_adjunct.save()
                        counts['adjuncts']['created'] += 1
                    else:
                        counts['adjuncts']['skipped'] += 1

            if 'FERMENTABLES' in parsed.keys():
                counts['fermentables'] = {'created': 0, 'skipped':0}
                counts['fermentable_types'] = {'created': 0, 'skipped': 0}
                for ferm_data in parsed['FERMENTABLES']['FERMENTABLE']:
                    fermentable = F()
                    fermentable.load(ferm_data)
                    if not fermentable.type:
                        # Apparently, Honey doesn't have a 'type' in BeerSmith export
                        # Set it.
                        if "honey" in fermentable.name.lower():
                            fermentable.type = "Honey"
                    fermentable_type, created = get_fermentable_type(fermentable.type)
                    if created:
                        counts['fermentable_types']['created'] += 1
                    else:
                        counts['fermentable_types']['skipped'] += 1
                    obj_fermentable = Fermentable()
                    obj_fermentable.type = fermentable_type
                    obj_fermentable.sugar_content = fermentable.brix
                    obj_fermentable.potential = fermentable.potential
                    obj_fermentable.color = fermentable.color
                    obj_fermentable.name = fermentable.name
                    obj_fermentable.version = fermentable.version
                    obj_fermentable.description = fermentable.notes
                    if len(Fermentable.objects.filter(name__iexact=fermentable.name)) == 0:
                        obj_fermentable.save()
                        counts['fermentables']['created'] += 1
                    else:
                        counts['fermentables']['skipped'] += 1


            if 'YEASTS' in parsed.keys():
                counts['yeasts'] = {'created': 0, 'skipped': 0}
                for yeast_data in parsed['YEASTS']['YEAST']:
                    yeast = Y()
                    yeast.load(yeast_data)
                    obj_yeast = Yeast()
                    obj_yeast.name = yeast.name
                    obj_yeast.version = yeast.version
                    obj_yeast.type = yeast.type
                    obj_yeast.form = yeast.form.lower
                    obj_yeast.min_temp = Quantity(yeast.min_temp, "degC")
                    obj_yeast.max_temp = Quantity(yeast.max_temp, "degC")
                    obj_yeast.flocculation = yeast.flocculation
                    obj_yeast.attenuation = yeast.attenuation
                    obj_yeast.notes = yeast.notes
                    if len(Yeast.objects.filter(name__iexact=yeast.name)) == 0:
                        obj_yeast.save()
                        counts['yeasts']['created'] += 1
                    else:
                        counts['yeasts']['skipped'] += 1


    else:
        return render(request, 'batchthis/admin/import.html')

    if counts is not None:
        messages.success(request, counts)
    return render(request, 'batchthis/admin/import.html')

def admin_initialize(request):
    """
    Used to create groups and initialize (or re-initialize).
    :param request:
    :return:
    """
    if request.method == "POST":
        winemaster_group, wm_created = Group.objects.get_or_create(name="Wine Masters")
        vintner_group, v_created = Group.objects.get_or_create(name="Vintners")
        production_group, prod_created = Group.objects.get_or_create(name="Production Floor")
        inventory_group, inv_created = Group.objects.get_or_create(name="Inventory Group")
        accounting_group, act_created = Group.objects.get_or_create(name="Accounting Group")

        group_results = {"winemaster_group": wm_created, "vintner_group": v_created,
                   "production_group": prod_created, "inventory_group": inv_created,
                   "accounting_group": act_created}

