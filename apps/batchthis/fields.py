import pdb

import re

from quantityfield.fields import QuantityField, QuantityFormField
from pint import Quantity
from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
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
        # set_base_units() changes self.base_units per value; a bare stored number is
        # always read in the units the field was declared with.
        self.declared_base_units = base_units
        super().__init__(base_units, *args, unit_choices=unit_choices, **kwargs)

    def deconstruct(self):
        # Used for migrations.  Undo what you added in __init__()
        name, path, args, kwargs = super().deconstruct()
        del kwargs['max_length']
        #del kwargs['verbose_name']
        return name, path, args, kwargs

    def set_base_units(self, value):
        # The project's registry (settings.DJANGO_PINT_UNIT_REGISTER), which defines sg/bx.
        if isinstance(value, str):
            quantity = self.ureg.Quantity(1, value)
        else:
            quantity = value
        base_unit_quantity = self.ureg.Quantity(1, self.base_units)
        if not base_unit_quantity.is_compatible_with(quantity):
            # We have a mismatch.  Identify dimensionality, and reset base_unit
            # all base_units to be stored as metric
            if quantity.check('[volume]'):
                self.base_units = 'liters'
            elif quantity.check('[mass]'):
                self.base_units = 'kilograms'
            elif quantity.check('[temperature]'):
                self.base_units = 'degC'
            elif quantity.check('[time]'):
                self.base_units = 'second'
            elif quantity.check('[Sucrose]'):
                # Specific gravity. Never converted to Brix - see the refractometer tool.
                self.base_units = 'sg'
            elif quantity.check('[mass] / [volume]'):
                self.base_units = 'milligram / liter'
            elif quantity.dimensionless:
                # ppm, or a plain number such as pH.
                self.base_units = 'dimensionless'
            else:
                raise ValueError(f"Not compatible base_unit: {self.base_units}.  Attempted 'mass', 'volume', 'temp', 'time', 'sg', 'concentration' and 'dimensionless': {quantity}")

    def createDescriptiveMarkup(self, value):
        if isinstance(value, str):
            # As typed first (unit symbols are case-sensitive: degF, mL), then lowercased.
            try:
                quantity = self.ureg.Quantity(value)
            except Exception:
                quantity = self.ureg.Quantity(value.lower())
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
        # Quantity(magnitude, units) rather than getattr(ureg, units): base units can be
        # compound ("milligram / liter").
        base_quantity = self.ureg.Quantity(float(converted_value), self.base_units)
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
        if isinstance(value, (Quantity, str)):
            return self.createDescriptiveMarkup(value)
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

        if isinstance(value, (float, int)):
            # Nothing to parse.  Assume the declared base_units
            return self.ureg.Quantity(float(value), self.declared_base_units)
        elif len(value.split(":")) == 1:
            # Old style, no ":unit" part: a bare number is in base units; text
            # like "6 gallon" is converted to markup and read back.
            try:
                return self.ureg.Quantity(float(value), self.declared_base_units)
            except ValueError:
                return self.convertFromDescriptiveMarkup(self.createDescriptiveMarkup(value))

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




class MeasurementField(forms.CharField):
    """
    Base for form fields that take a measurement typed as text ("6 gallons",
    "4 g"). Returns the Quantity exactly as entered - DescriptiveQuantityField
    stores it in metric and gives back the entered unit. Rejects a bare number
    ("Units are required."), units outside `dimensions`, anything that isn't an
    amount + unit, and zero/negative amounts. Subclasses set `dimensions` and
    the `wrong_kind` / `invalid_amount` messages.
    """
    dimensions: tuple[str, ...] = ()
    allow_zero = False  # e.g. TimeSpanField: 0 = a point-in-time plan step
    default_error_messages = {
        'units_required': "Units are required.",
        'not_positive': "Enter an amount greater than zero.",
    }
    _STARTS_WITH_NUMBER = re.compile(r'^[-+]?(\d|\.\d)')

    def __init__(self, *, placeholder: str = "", **kwargs):
        kwargs.setdefault('widget', TextInput(attrs={'placeholder': placeholder} if placeholder else {}))
        super().__init__(**kwargs)

    def to_python(self, value):
        text = super().to_python(value)
        if text in self.empty_values:
            return None
        if not self._STARTS_WITH_NUMBER.match(text):
            logger.debug(f"{type(self).__name__}: no amount in {text!r}")
            raise ValidationError(self.error_messages['invalid_amount'], code='invalid_amount')
        ureg = settings.DJANGO_PINT_UNIT_REGISTER
        try:
            # As typed first (unit symbols are case-sensitive: degF, mL), then
            # lowercased so "6Gallons" / "20 Liters" still parse.
            try:
                quantity = ureg.Quantity(text)
            except Exception:
                quantity = ureg.Quantity(text.lower())
        except Exception:
            logger.debug(f"{type(self).__name__}: could not parse {text!r}")
            raise ValidationError(self.error_messages['invalid_amount'], code='invalid_amount')
        # A bare number parses with the unit "dimensionless". (Not .unitless: pint also
        # calls ppm/percent unitless, and those are units - just the wrong kind.)
        if not isinstance(quantity, ureg.Quantity) or str(quantity.units) in ('', 'dimensionless'):
            raise ValidationError(self.error_messages['units_required'], code='units_required')
        if not any(quantity.check(dimension) for dimension in self.dimensions):
            raise ValidationError(self.error_messages['wrong_kind'], code='wrong_kind')
        if quantity.magnitude < 0 or (quantity.magnitude == 0 and not self.allow_zero):
            raise ValidationError(self.error_messages['not_positive'], code='not_positive')
        return quantity

    def has_changed(self, initial, data) -> bool:
        # Compare measurements, not text: initial "6.00 gallon" and a re-submitted
        # "6 gallons" are the same. A different unit counts as a change (it
        # changes how the value is shown).
        try:
            new = self.to_python(data)
            old = self.to_python(initial) if isinstance(initial, str) else initial
        except ValidationError:
            return True
        if new is None or old is None:
            return (new is None) != (old is None)
        return str(new.units) != str(old.units) or abs(new.magnitude - old.magnitude) > 1e-9


class VolumeField(MeasurementField):
    """A volume ("6 gallons", "20 L") - e.g. batch size, vessel capacity/fill."""
    dimensions = ('[volume]',)
    default_error_messages = {
        'wrong_kind': "Use a volume unit: gallons or liters.",
        'invalid_amount': "Enter an amount and a unit, e.g. 6 gallons or 20 liters.",
    }

    def __init__(self, *, placeholder: str = "e.g. 6 gallons", **kwargs):
        super().__init__(placeholder=placeholder, **kwargs)


class AmountField(MeasurementField):
    """A weight or volume ("4 g", "0.5 oz", "5 ml") - e.g. a batch addition's amount."""
    dimensions = ('[mass]', '[volume]')
    default_error_messages = {
        'wrong_kind': "Use a weight or volume unit, e.g. 4 grams or 5 ml.",
        'invalid_amount': "Enter an amount and a unit, e.g. 4 grams or 5 ml.",
    }

    def __init__(self, *, placeholder: str = "e.g. 4 grams", **kwargs):
        super().__init__(placeholder=placeholder, **kwargs)


class TimeSpanField(MeasurementField):
    """
    A duration ("14 days", "2 weeks", "36 hours") - e.g. a plan step's planned
    time. 0 is allowed: it marks a point-in-time step, same as leaving it blank.
    """
    dimensions = ('[time]',)
    allow_zero = True
    default_error_messages = {
        'wrong_kind': "Use a time unit, e.g. 14 days or 2 weeks.",
        'invalid_amount': "Enter an amount and a unit, e.g. 14 days or 2 weeks.",
    }

    def __init__(self, *, placeholder: str = "e.g. 14 days", **kwargs):
        super().__init__(placeholder=placeholder, **kwargs)


class ReadingField(MeasurementField):
    """
    A test reading typed as text: "1.050 sg", "68 F", "30 ppm", "6.5 g/L", "3.40", "12 bx".
    Unlike VolumeField/AmountField it doesn't decide which units are allowed -
    that depends on the test type chosen alongside it, so BatchTestForm checks
    it against the type's ReadingSpec (models.READING_SPECS). A bare number is
    returned as dimensionless; "F"/"C" shorthand means °F/°C (never farads).
    """
    default_error_messages = {
        'invalid_amount': "Enter a number, e.g. 3.40.",
    }
    _TEMPERATURE_SHORTHAND = re.compile(r'^\s*([-+]?(?:\d+\.?\d*|\.\d+))\s*°?\s*([FfCc])\s*$')
    _BRIX = re.compile(r'^\s*([-+]?(?:\d+\.?\d*|\.\d+))\s*°?\s*(bx|brix)\s*$', re.IGNORECASE)

    def __init__(self, *, placeholder: str = "e.g. 1.050", **kwargs):
        super().__init__(placeholder=placeholder, **kwargs)

    def to_python(self, value):
        text = forms.CharField.to_python(self, value)
        if text in self.empty_values:
            return None
        shorthand = self._TEMPERATURE_SHORTHAND.match(text)
        brix = self._BRIX.match(text)
        if shorthand:
            number, scale = shorthand.groups()
            text = f"{number} {'degF' if scale.upper() == 'F' else 'degC'}"
        elif brix:
            # "12 bx", "12 brix", "12 °Bx" -> the registry's Brix unit (case-sensitive there).
            text = f"{brix.group(1)} Brix"
        if not self._STARTS_WITH_NUMBER.match(text):
            logger.debug(f"ReadingField: no number in {text!r}")
            raise ValidationError(self.error_messages['invalid_amount'], code='invalid_amount')
        ureg = settings.DJANGO_PINT_UNIT_REGISTER
        try:
            try:
                quantity = ureg.Quantity(text)
            except Exception:
                quantity = ureg.Quantity(text.lower())
        except Exception:
            logger.debug(f"ReadingField: could not parse {text!r}")
            raise ValidationError(self.error_messages['invalid_amount'], code='invalid_amount')
        if not isinstance(quantity, ureg.Quantity):
            quantity = ureg.Quantity(float(quantity))
        return quantity
