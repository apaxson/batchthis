from apps.batchthis.lib.utils import Utils
from django.http import HttpResponse, HttpResponseForbidden


def utilities(request, action, **kwargs):
    """
    :param action:
        sgToBrix
        brixToSg
        potentialABV
        refractometerCorrection
        dilution
        innoculationRate
    :param kwargs:
    :return:
    """
    if not request.user.is_authenticated:
        # If no user, then this request is outside
        # the context of the web page.  Disregard.
        return HttpResponseForbidden()

    if action == "sgToBrix":
        return HttpResponse(Utils.sgToBrix(kwargs.get('sg')), content_type="application/json")

    if action == "brixToSg":
        return HttpResponse(Utils.brixToSg(kwargs.get('brix')), content_type="application/json")

    if action == "potentialABV":
        return HttpResponse(Utils.potentialABV(
            startBrix=kwargs.get('startBrix'),
            endBrix=kwargs.get('endBrix'),
            startSG=kwargs.get('startSG'),
            endSG=kwargs.get('endSG'),
            yeastPotential=kwargs.get('yeastPotential')
        ), content_type="application/json")

    if action == "refractometerCorrection":
        return HttpResponse(Utils.refractometerCorrection(
            startSG=kwargs.get('startSG'),
            startBrix=kwargs.get('startBrix'),
            currentSG=kwargs.get('currentSG'),
            currentBrix=kwargs.get('currentBrix')
        ), content_type="application/json")

    if action == "dilution":
        return HttpResponse(Utils.dilution(
            startConcentration=kwargs.get('startConcentration'),
            startVolume=kwargs.get('startVolume'),
            endConcentration=kwargs.get('endConcentration'),
            endVolume=kwargs.get('endVolume')
        ), content_type="application/json")

    if action == "innoculationRate":
        return HttpResponse(Utils.innoculationRate(
            startBrix=kwargs.get('startBrix'),
            liters=kwargs.get('liters')
        ), content_type="application/json")

