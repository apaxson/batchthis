"""
  Copyright 2021 Stones River Meadery (aaron@stonesrivermead.com)

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
"""
import re
from pint import UnitRegistry
import logging

logger = logging.getLogger(__name__)

class InvalidUnitsException(Exception):
    # Used when there is a mismatch in units
    def __init__(self, message):
        self.message = message
        super().__init__(message)

    def __str__(self):
        return f"{self.message}"

class InvalidPintUnitsException(InvalidUnitsException):
    # Used when there is a mismatch in units
    def __init__(self, message):
        self.message = message
        super().__init__(message)


class InvalidArguments(Exception):
    # Used when the wrong arguments are used
    pass


class Utils:
    @staticmethod
    def parseMeasurementString(str_data, dimensionality=None):
        ureg = UnitRegistry()
        # Clean up and validate accepted measurements
        measurement = ureg(str_data.lower())
        if dimensionality:
            if not measurement.check(dimensionality):
                msg = f"Parsed {str_data} to be {measurement}.  Expected to be of type: {str(dimensionality)}"
                logger.warning(msg)
                raise InvalidPintUnitsException(msg)
        return measurement

    def strToIntFormat(str):
        # Pull out int + metric.  i.e. "100ml" will return (100,'ml')
        p = re.compile("([\\d]*)([\\w\\W]*)")
        data = p.match(str).groups()
        return int(data[0]),data[1].strip()

    def sgToBrix(sg):
        brix = (((182.4601 * sg -775.6821) * sg +1262.7794) * sg - 669.5622)
        return brix

    def brixToSg(brix):
        sg = (brix / (258.6 - ((brix/258.2) * 227.1))) + 1
        return sg

    def potentialABV(startBrix=None,endBrix=None,startSG=None,endSG=None, yeastPotential=None):
        '''
        Determines the estimated ABV based on starting sugar, ending sugar, and yeast potential
        Needs a starting SG or a starting Brix

        Updated using new formula from Ritchie Products Ltd, (Zymurgy, Summer 1995, vol. 18, no. 2)
        -Michael L. Hall’s article Brew by the Numbers: Add Up What’s in Your Beer, and Designing Great Beers by Daniels.

        :param startBrix: int
        :param endBrix: int
        :param startSG: float
        :param endSG: float
        :param yeastPotential: int (Yeast estimated ABV limit as percentage)

        :returns
        Tuple of Floats.  [0] is estimated ABV.  [1] is estimated specific gravity after
        '''
        if not startSG:
            startSG = Utils.brixToSg(startBrix)
        if not endSG:
            if endBrix:
                endSG = Utils.brixToSg(endBrix)
            else:
                endSG = 1.000
        #abv = (startSG - endSG) * 131.25
        abv = (76.08 * (startSG-endSG) / (1.775-startSG)) * (endSG / 0.794)
        if yeastPotential:
            if abv<yeastPotential:
                return abv,endSG
            else:
                gravityPointABV = 0.12379669224609573 #calculated ABV per gravity point (.001)
                remainingABV = abv-yeastPotential
                remainingSugar = ((remainingABV/gravityPointABV)*.001) #convert to gravity points
                return yeastPotential, remainingSugar + endSG
        return abv,endSG

    def refractometerCorrection(startSG=None,startBrix=None,currentSG=None,currentBrix=None):
        # from Petr Novotny, "Revisiting The Refractometer", Zymurgy Issue July/Aug 2017
        if not startBrix:
            startBrix = Utils.sgToBrix(startSG)
        if not currentBrix:
            currentBrix = Utils.sgToBrix(currentSG)
        currentGravity = -0.002349*startBrix + 0.006276*currentBrix + 1
        abv = Utils.potentialABV(startBrix=startBrix,endBrix=currentBrix)
        return currentGravity,abv[0]

    def dilution(startConcentration=None, startVolume=None, endConcentration=None, endVolume=None):
        # Check if we have the proper variables/formatting to solve for
        # Based on C1V1 = C2V2
        varCount = 0
        endConcentrationStr = ''
        startConcentrationStr = ''
        endVolumeStr = ''
        startVolumeStr = ''
        if startConcentration:
            varCount +=1
            if isinstance(startConcentration,str):
                startConcentration,startConcentrationStr = Utils.strToIntFormat(startConcentration)
        if endConcentration:
            varCount +=1
            if isinstance(endConcentration,str):
                endConcentration,endConcentrationStr = Utils.strToIntFormat(endConcentration)
        if startVolume:
            varCount +=1
            if isinstance(startVolume,str):
                startVolume,startVolumeStr = Utils.strToIntFormat(startVolume)
        if endVolume:
            varCount +=1
            if isinstance(endVolume,str):
                endVolume,endVolumeStr = Utils.strToIntFormat(endVolume)
        if varCount != 3:
            raise InvalidArguments

        #TODO Use units to do self-conversions rather than requiring same units in params
        if not startConcentration:
            if endVolumeStr != startVolumeStr:
                raise InvalidUnitsException
            startConcentration = (endConcentration * endVolume) / startVolume
            return str(startConcentration) + endConcentrationStr
        if not endConcentration:
            if endVolumeStr != startVolumeStr:
                raise InvalidUnitsException
            endConcentration = (startConcentration * startVolume) / endVolume
            return str(endConcentration) + startConcentrationStr
        if not startVolume:
            if endConcentrationStr != startConcentrationStr:
                raise InvalidUnitsException
            startVolume = (endConcentration * endVolume) / startConcentration
            return str(startVolume) + endVolumeStr
        if not endVolume:
            if endConcentrationStr != startConcentrationStr:
                raise InvalidUnitsException
            endVolume = (startConcentration * startVolume) / endConcentration
            return str(endVolume) + startVolumeStr

    def innoculationRate(startBrix,liters,yeastreq=None):
        """
        :param startBrix:
        :param liters:
        :return: yeast amount in grams
        :return: hydration volume amount in mL
        """
        # 25g/hL or 28g/hL is most typical recommendation from manufacturers
        # Should equate to ~5mil cells/mL
        # water is 10x in mL
        hL = liters/100
        if startBrix <= 25:
            yeastRate = 25*hL # in grams
        else:
            yeastRate = 28*hL # in grams

        hydration = yeastRate*10 #in mL
        return yeastRate,hydration
