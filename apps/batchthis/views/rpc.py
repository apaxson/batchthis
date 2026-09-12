from apps.batchthis.lib.utils import Utils, InvalidArguments, InvalidUnitsException
from django.http import JsonResponse, HttpResponseForbidden, HttpResponseBadRequest


def _float_or_none(value):
    return float(value) if value not in (None, '') else None


def utilities(request, action):
    """
    GET /rpc/utils/<action>/?<params>

    :param action:
        sgToBrix
        brixToSg
        potentialABV
        refractometerCorrection
        dilution
        innoculationRate
    :return: JsonResponse
    """
    if not request.user.is_authenticated:
        # If no user, then this request is outside
        # the context of the web page.  Disregard.
        return HttpResponseForbidden()

    params = request.GET

    try:
        return _dispatch(action, params)
    except (InvalidArguments, InvalidUnitsException, TypeError, ValueError) as e:
        return HttpResponseBadRequest(f"Invalid parameters for {action}: {e}")


def _dispatch(action, params):
    if action == "sgToBrix":
        return JsonResponse({'brix': Utils.sgToBrix(_float_or_none(params.get('sg')))})

    if action == "brixToSg":
        return JsonResponse({'sg': Utils.brixToSg(_float_or_none(params.get('brix')))})

    if action == "potentialABV":
        abv, endSG = Utils.potentialABV(
            startBrix=_float_or_none(params.get('startBrix')),
            endBrix=_float_or_none(params.get('endBrix')),
            startSG=_float_or_none(params.get('startSG')),
            endSG=_float_or_none(params.get('endSG')),
            yeastPotential=_float_or_none(params.get('yeastPotential'))
        )
        return JsonResponse({'abv': abv, 'endSG': endSG})

    if action == "refractometerCorrection":
        currentGravity, abv = Utils.refractometerCorrection(
            startSG=_float_or_none(params.get('startSG')),
            startBrix=_float_or_none(params.get('startBrix')),
            currentSG=_float_or_none(params.get('currentSG')),
            currentBrix=_float_or_none(params.get('currentBrix'))
        )
        return JsonResponse({'sg': currentGravity, 'abv': abv})

    if action == "dilution":
        # startConcentration/endConcentration/startVolume/endVolume may carry
        # a unit suffix (e.g. "100ml"), so pass the raw query strings through.
        result = Utils.dilution(
            startConcentration=params.get('startConcentration'),
            startVolume=params.get('startVolume'),
            endConcentration=params.get('endConcentration'),
            endVolume=params.get('endVolume')
        )
        return JsonResponse({'result': result})

    if action == "innoculationRate":
        yeastGrams, hydrationML = Utils.innoculationRate(
            startBrix=_float_or_none(params.get('startBrix')),
            liters=_float_or_none(params.get('liters'))
        )
        return JsonResponse({'yeastGrams': yeastGrams, 'hydrationML': hydrationML})

    return HttpResponseBadRequest(f"Unknown action: {action}")
