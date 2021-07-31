class Utils:
    def sgToBrix(sg):
        #TODO
        pass

    def brixToSg(brix):
        sg = (brix / (258.6 - ((brix/258.2) * 227.1))) + 1
        return sg

    def abvCalc(startBrix=None,endBrix=None,startSG=None,endSG=None):
        #TODO
        pass

    def dilution(startConcentration=None, startVolume=None, endConcentration=None, endVolume=None):
        # Check if we have the proper variables to solve for
        varCount = 0
        if startConcentration: varCount +=1
        if endConcentration: varCount +=1
        if startVolume: varCount +=1
        if endVolume: varCount +=1
        if varCount != 3:
            #TODO Raise Exception
            print("Wrong var count")
            return None
        if not startConcentration:
            startConcentration = (endConcentration * endVolume) / startVolume
            return startConcentration
        if not endConcentration:
            endConcentration = (startConcentration * startVolume) / endVolume
            return endConcentration
        if not startVolume:
            startVolume = (endConcentration * endVolume) / startConcentration
            return startVolume
        if not endVolume:
            endVolume = (startConcentration * startVolume) / endConcentration
            return endVolume
