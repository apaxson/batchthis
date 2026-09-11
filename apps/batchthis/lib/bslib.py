import xmltodict
import pathlib
from datetime import datetime
import re
# &ldquo;
# &ndash;
# &rsquo;
# &AElig;
#

class TooManyRecipesError(Exception):
    """Exception raised when trying to parse 1 recipe, but received more.
      Attributes:
          num: Number of recipes received
    """
    def __init__(self,num):
        self.num = num
        self.message = f"Too many recipes received.  Received {self.num}.  Expected 1"
        super().__init__(self.message)

    def __str__(self):
        return self.message

class Adjunct(object):
    """
    Any item that is not fermentable, added to a recipe, such as fining agents, sulfites, flavoring, etc
    """
    def __init__(self):
        self.name = None
        self.version = 0
        self.type = None #TODO: Refactor to own object for normalization
        self.use = None
        self.amount = 0
        self.time = 0
        self.amount_is_weight = False
        self.use_for = None
        self.notes = None
        self.display_amount = None
        self.display_time = None
        self.batchsize = 0

    def __str__(self):
        return self.name

    def load(self,data):
        """
         Used to do a one time load of Adjunct from dict
         :param data: {dict}
         :return: None
        """
        self.name = data["NAME"]
        self.version = data["VERSION"]
        self.type = data["TYPE"]
        self.use = data["USE"]
        self.amount = data["AMOUNT"]
        self.time = data["TIME"]
        if data["AMOUNT_IS_WEIGHT"].lower() == "false":
            self.amount_is_weight = False
        if data["AMOUNT_IS_WEIGHT"].lower() == "true":
            self.amount_is_weight = True
        self.notes = data["NOTES"]
        self.display_amount = data["DISPLAY_AMOUNT"]
        self.batchsize = data["BATCH_SIZE"]
        self.display_time = data["DISPLAY_TIME"]

class Yeast(object):
    def __init__(self):
        self.name = None
        self.version = 0
        self.type = None
        self.form = None
        self.amount = 0
        self.min_temp = 0.0
        self.max_temp = 0.0
        self.flocculation = None
        self.attenuation = 0.0
        self.notes = None
        self.amount_is_weight = None

    def __str__(self):
        return self.name

    def load(self,data):
        """
        Used to do a one time load of Adjunct from dict
            :param data: {dict}
            :return: None
        """
        self.amount = data["AMOUNT"]
        self.attenuation = data['ATTENUATION']
        self.flocculation = data['FLOCCULATION']
        self.form = data['FORM']
        self.max_temp = data['MAX_TEMPERATURE']
        self.min_temp = data['MIN_TEMPERATURE']
        self.name = data['NAME']
        self.notes = data['NOTES']
        self.type = data['TYPE']
        self.version = data['VERSION']
        self.amount_is_weight = data["AMOUNT_IS_WEIGHT"]


class Fermentable(object):
    """
    Any item that adds sugar to a recipe
    """
    def __init__(self):
        self.name = None
        self.version = 0
        self.type = None
        self.amount = 0 # Kilograms / Liters
        self.brix = 0 # Brix
        self.color = 0 #SRM
        self.notes = None
        self.potential = 0.0 # Specific Gravity

    def __str__(self):
        return self.name

    def load(self,data):
        """
         Used to do a one time load of Adjunct from dict
         :param data: {dict}
         :return: None
        """
        if data.keys() >= {'NAME'}:
            # Beersmith XML file
            self.amount = data["AMOUNT"]
            self.brix = data["YIELD"]
            self.color = data["COLOR"]
            self.name = data["NAME"]
            self.notes = data["NOTES"]
            self.potential = data["POTENTIAL"]
            self.type = data["TYPE"]
            self.version = data["VERSION"]
        else:
            # Beersmith BSMX file
            self.name = data['F_G_NAME']
            self.amount = data['']


class Recipe(object):
    def __init__(self):
        self.file = None
        self.datecreated = None
        self.datemod = None
        self.rawdata = None
        self.parsed = {}
        self.name = None
        self.brewer = None
        self.version = 0
        self.batchsize = 0
        self.fermentables = []
        self.adjuncts = []
        self.yeasts = []
        self.stylename = None
        self.stylecat = None
        self.stylecatnum = None
        self.styleletter = None
        self.styleguide = None
        self.styletype = None
        self.estabv = 0
        self.calories = 0
        self.date = None
        self.estog = 0
        self.estfg = 0
        self.og = 0.0
        self.fg = 0.0
        self.abv = 0.0

    def iterateItems(self, src, className=None):
        """
        Identifies if the following is a list (multiple) or a dict (single).
        :param src: data to review
        :return: List
        """
        items = []
        classfull_items = []
        if className:
            if type(src) is list:
                for item in src:
                    instance = className()
                    instance.load(item)
                    classfull_items.append(instance)
            else:
                instance = className()
                instance.load(src)
                classfull_items.append(instance)
        else:
            if type(src) is list:
                for item in src:
                    items.append(item)
            else:
                items.append(src)
        if className:
            return classfull_items
        else:
            return items

    def parse(self, file):
        self.file = file
        extension = pathlib.Path(file).suffix
        f = open(file, 'r')
        self.rawdata = f.read()
        # clean up expat errors on unrecognizable entities
        cleaned = self.rawdata.replace("&ldquo;", "").replace("&ndash;", "").replace("&rsquo;", "").replace("&AElig;","").replace("&rdquo;", "")
        self.parsed = xmltodict.parse(cleaned)
        f.close()
        if extension == ".xml":
            if type(self.parsed['RECIPES']['RECIPE']) is list:
                recipe_num = len(self.parsed['RECIPES']['RECIPE'])
                raise TooManyRecipesError(recipe_num)

            __recipe = self.parsed['RECIPES']['RECIPE']
            float_pattern = re.compile("[\d]+\.*[\d]*")
            self.datecreated = datetime.strptime(__recipe['DATE'], "%d %b %Y")
            self.name = __recipe['NAME']
            self.brewer = __recipe['BREWER']
            self.version = __recipe['VERSION']
            self.batchsize = __recipe['BATCH_SIZE']
            self.fermentables = self.iterateItems(__recipe['FERMENTABLES']['FERMENTABLE'], Fermentable)
            self.adjuncts = self.iterateItems(__recipe['MISCS']['MISC'],Adjunct)
            self.yeasts = self.iterateItems(__recipe['YEASTS']['YEAST'],Yeast)
            __style = __recipe['STYLE']
            self.stylename = __style['NAME']
            self.stylecat = __style['CATEGORY']
            self.stylecatnum = __style['CATEGORY_NUMBER']
            self.styleletter = __style['STYLE_LETTER']
            self.styleguide = __style['STYLE_GUIDE']
            self.styletype = __style['TYPE']
            _estabv = __recipe['EST_ABV']
            self.estabv = float(float_pattern.match(_estabv).group())
            _estog = __recipe['EST_OG']
            self.estog = float(float_pattern.match(_estog).group())
            _estfg = __recipe['EST_FG']
            self.estfg = float(float_pattern.match(_estfg).group())
            self.og = __recipe['OG']
            self.fg = __recipe['FG']
            self.abv = __recipe['ABV']

        if extension == ".bsmx":
            if type(self.parsed['Recipes']['Data']['Recipe']) is list:
                recipe_num = len(self.parsed['Recipes']['Data']['Recipe'])
                raise TooManyRecipesError(recipe_num)

            __recipe = self.parsed['Recipes']['Data']['Recipe']
            self.datecreated = datetime.strptime(__recipe['F_R_DATE'], "%Y-%m-%d")
            self.datemod = datetime.strptime(__recipe['_MOD_'])
            self.name = __recipe['F_R_NAME']