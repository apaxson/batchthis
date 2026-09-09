import pdb

from quantityfield.fields import QuantityField, QuantityFormField
from pint import Quantity
from django.forms.widgets import TextInput
from quantityfield.widgets import QuantityWidget
import logging
import pdb

logger = logging.getLogger(__name__)
class DescriptiveQuantityField(QuantityField):
    """
    We want to store the initial selected unit with the base_unit conversion,
    in order to stay consistent with the inputted data.  We will store the data in a charfield using
    markup:

    "11520:days"  base_unit = minutes.  selected_unit = days.  displayed: 8 days
    "18.9770589:gallons"  base_unit = liters.  selected_unit = gallons. displayed: 5 gallons

    if no markup, assume base_units

    """
    def __init__(self, base_units=None, unit_choices=None, *args, **kwargs):
        kwargs['max_length'] = 30
        self.selected_unit = None
        if not base_units:
            base_units = 'kilogram' #assume 'mass' dimensionality, but we'll override on save
        super().__init__(base_units, *args, unit_choices=unit_choices, **kwargs)

    def deconstruct(self):
        # Used for migrations.  Undo what you added in __init__()
        name, path, args, kwargs = super().deconstruct()
        del kwargs['max_length']
        #del kwargs['verbose_name']
        return name, path, args, kwargs

    def set_base_units(self, value):
        if isinstance(value, Quantity):
            quantity = value
        if isinstance(value,str):
            quantity = Quantity(1, value)
        base_unit_quantity = Quantity(1, self.base_units)
        if not base_unit_quantity.is_compatible_with(quantity):
            # We have a mismatch.  Identify dimensionality, and reset base_unit
            # all base_units to be stored as metric
            if quantity.check('[volume]'):
                self.base_units = 'liters'
            elif quantity.check('[mass]'):
                self.base_units = 'kilograms'
            elif quantity.check('[temperature]'):
                self.base_units = 'degC'
            else:
                raise ValueError(f"Not compatible base_unit: {self.base_units}.  Attempted 'mass', 'volume', and 'temp': {quantity}")

    def createDescriptiveMarkup(self, value):
        if isinstance(value, str):
            quantity = self.fix_unit_registry(Quantity(value.lower()))
        else:
            quantity = self.fix_unit_registry(Quantity(value))
        selected_unit = quantity.units
        self.set_base_units(quantity)
        converted_magnitude = quantity.to(self.base_units).magnitude
        formatted = str(converted_magnitude) + ":" + str(selected_unit)
        logger.debug(f"Markup Quantity Field to be: {formatted}")
        return formatted

    def convertFromDescriptiveMarkup(self, value):
        logger.debug(f"DB Value found: {type(value)} as {value}")
        logger.debug("Found quantity markup.  Converting: " + value)
        converted_value, selected_unit = value.split(':')
        self.selected_unit = selected_unit
        self.set_base_units(selected_unit)
        base_quantity = self.ureg.Quantity(float(converted_value) * getattr(self.ureg, self.base_units))
        logger.debug("converted to: " + str(base_quantity.to(selected_unit)))
        return base_quantity.to(selected_unit)

    def get_prep_value(self, value):
        """
        Prepare the data to be stored in the DB
        :param value:
        :return:
        """
        if value is None:
            return None
        if isinstance(value, Quantity):
            return self.createDescriptiveMarkup(value)
        elif isinstance(value, str):
            quantity = Quantity(value.lower())
            return self.createDescriptiveMarkup(quantity)
        else:
            return value

    def from_db_value(self, value, *args, **kwargs):
        """
        Prepare the data pulling from the DB
        :param value:
        :param args:
        :param kwargs:
        :return:
        """
        if value is None:
            return None

        if isinstance(value, float):
            # Nothing to parse.  Assume base_units
            return self.ureg.Quantity(value * getattr(self.ureg, self.base_units))
        elif len(value.split(":")) == 1:
            #old style.  Convert text to markdown
            formatted_quantity = self.createDescriptiveMarkup(value)
            data_value = self.convertFromDescriptiveMarkup(formatted_quantity)
            return

        else:
            return self.convertFromDescriptiveMarkup(value)

    def to_python(self, value):
        if isinstance(value, Quantity):
            return self.fix_unit_registry(value)

        if value is None:
            return None

        return self.from_db_value(value)

    def value_to_string(self, obj):
        value = self.value_from_object(obj)
        return str(self.get_prep_value(value))



class PrecisionQuantityWidget(QuantityWidget):
    def __init__(self, *, attrs=None, base_units=None, allowed_types=None, precision=None):
        self.precision = precision
        self.__name__ = self.__class__.__name__  # Required for super().is_special_admin_widget
        super().__init__(attrs=attrs, base_units=base_units, allowed_types=allowed_types)

    def decompress(self, value):
        if value:
            if isinstance(value, Quantity):
                logger.debug(str(value))
                if self.precision:
                    precision_format = '{:.' + str(self.precision) + "f}"
                    #return [precision_format.format(value.magnitude), value.units]
                    return f"{precision_format.format(value.magnitude)} {value.units}"
                return [value.magnitude, value.units]
            else:
                # We assume that the given value is a proper number,
                # ready to be rendered
                return [value, self.base_units]
        return [None, self.base_units]

class PrecisionTextWidget(TextInput):
    def __init__(self, *, attrs=None, precision=None, base_units=None):
        self.precision = precision
        self.base_units = base_units
        self.__name__ = self.__class__.__name__ # Required for super().is_special_admin_widget
        super().__init__(attrs=attrs)

    #def decompress(self,value):
    def format_value(self, value):
        logger.debug("Found PrecisionTextWidget()")
        if value:
            if isinstance(value, Quantity):
                if self.precision:
                    precision_format = "{:." + str(self.precision) + "f}"
                    logger.debug(f"Applying Quantity() precision of {str(self.precision)} to value: {value}")
                    return f"{precision_format.format(value.magnitude)}"
                else:
                    logger.debug(f"Returning Quantity() of {value}")
                    return f"{value.magnitude}"
            else:
                logger.debug(f"No Quantity().")
                return f"{value}"
        else:
            return None


class DescriptiveQuantityFormField(QuantityFormField):

    precision = None
    def __init__(self, *args, precision=None, **kwargs):
        if precision:
            if isinstance(precision, int):
                self.precision = precision

        super().__init__(*args, **kwargs)



    def to_python(self, value):
        print("formfield: to_python(): " + str(value))
        super().to_python(value)

    def validate(self, value):
        super().validate(value)
    def prepare_value(self, value):
        print("formfield: prepare_value(): " + str(value))
        if self.precision:
            pass
        return value

    def clean(self, value):
        if value is None:
            return None
        if isinstance(value, list):
            if value[0] == '':
                return None
            super().clean(value)
            mag = float(value[0])
            sel_unit = value[1]
            val = self.ureg.Quantity(mag * getattr(self.ureg, sel_unit))
            logger.debug("Converted field data: " + str(value) + " to Quantity: " + str(val))
            return val


