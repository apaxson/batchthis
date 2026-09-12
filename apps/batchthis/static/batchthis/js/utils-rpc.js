/**
 * Shared client for the /rpc/utils/<action>/ endpoint (apps/batchthis/views/rpc.py),
 * which exposes the formulas in apps/batchthis/lib/utils.py's Utils class as JSON.
 * Any page can call these instead of re-implementing the calculations in JS.
 */
var BatchUtilsRPC = (function ($) {
  function call(action, params) {
    var url = RPC_UTILS_URL_TEMPLATE.replace('__ACTION__', action);
    return $.getJSON(url, params || {});
  }

  return {
    sgToBrix: function (sg) {
      return call('sgToBrix', {sg: sg});
    },
    brixToSg: function (brix) {
      return call('brixToSg', {brix: brix});
    },
    potentialABV: function (params) {
      // params: {startSG, endSG} or {startBrix, endBrix}, optional yeastPotential
      return call('potentialABV', params);
    },
    refractometerCorrection: function (params) {
      return call('refractometerCorrection', params);
    },
    dilution: function (params) {
      return call('dilution', params);
    },
    innoculationRate: function (params) {
      return call('innoculationRate', params);
    }
  };
})(jQuery);
