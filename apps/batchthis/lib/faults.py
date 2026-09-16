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
import logging

logger = logging.getLogger(__name__)


class FaultRule:
    """
    A threshold check against a batch's most recent reading of one BatchTestType.

    See docs/flagged-notifications.md for how to add rules, wire them to Celery,
    or move them into a database-backed model.
    """

    def __init__(self, test_shortid, label, message, minimum=None, maximum=None, severity='warning'):
        self.test_shortid = test_shortid
        self.label = label
        self.message = message
        self.minimum = minimum
        self.maximum = maximum
        self.severity = severity

    def _latest_test(self, batch):
        return batch.tests.filter(type__shortid=self.test_shortid).order_by('-datetime').first()

    def _breach(self, minimum, maximum, value):
        return (minimum is not None and value < minimum) or \
               (maximum is not None and value > maximum)

    def _flag(self, batch, test):
        return {
            'batch': batch,
            'test': test,
            'severity': self.severity,
            'label': self.label,
            'message': self.message.format(batch=batch, value=test.value),
        }

    def evaluate(self, batch):
        test = self._latest_test(batch)
        if test is None:
            return None
        if not self._breach(self.minimum, self.maximum, test.value):
            return None
        return self._flag(batch, test)


class StagedFaultRule(FaultRule):
    """
    Like FaultRule, but the acceptable min/max pair moves over the batch's life
    instead of being fixed - e.g. pH is expected to sit in a wide range right
    after pitching, then narrow as fermentation settles.

    `breakpoints` is an ordered list of (hours_since_start, minimum, maximum)
    tuples. Between two breakpoints the bounds are linearly interpolated (a
    gradual transition); before the first breakpoint and after the last, the
    nearest breakpoint's bounds hold flat. A flat stage is just two breakpoints
    with the same minimum/maximum, e.g.:

        breakpoints=[(0, 3.2, 4.6), (48, 3.2, 4.6), (384, 3.0, 3.6)]

    holds 3.2-4.6 through hour 48, then tapers linearly to 3.0-3.6 by hour 384.

    `bounds_at()` is also used by the batch detail charts to draw the same
    curve the flag logic checks against, so they can't drift out of sync -
    see views/main.py: _build_series and cellar-ledger.js.
    """

    def __init__(self, test_shortid, label, message, breakpoints, severity='warning'):
        super().__init__(test_shortid, label, message, severity=severity)
        self.breakpoints = sorted(breakpoints, key=lambda bp: bp[0])

    def bounds_at(self, elapsed_hours):
        bps = self.breakpoints
        if elapsed_hours <= bps[0][0]:
            return bps[0][1], bps[0][2]
        if elapsed_hours >= bps[-1][0]:
            return bps[-1][1], bps[-1][2]
        for (h0, min0, max0), (h1, min1, max1) in zip(bps, bps[1:]):
            if h0 <= elapsed_hours <= h1:
                frac = (elapsed_hours - h0) / (h1 - h0) if h1 != h0 else 0
                return min0 + (min1 - min0) * frac, max0 + (max1 - max0) * frac

    def evaluate(self, batch):
        test = self._latest_test(batch)
        if test is None or batch.startdate is None:
            return None
        elapsed_hours = (test.datetime - batch.startdate).total_seconds() / 3600
        minimum, maximum = self.bounds_at(elapsed_hours)
        if self._breach(minimum, maximum, test.value):
            return self._flag(batch, test)
        return None


# Shipped rules. Each one only looks at a batch's latest reading of a single test
# type, so evaluating the whole list is cheap. Add new rules here.
FAULT_RULES = [
    FaultRule(
        test_shortid='so2',
        label='Free SO₂ low',
        message=(
            'Free SO₂ has fallen to {value:g} ppm, below the 10 ppm floor recommended '
            'once a batch is resting in secondary. Below that line, oxidation and microbial '
            'spoilage become more likely.'
        ),
        minimum=10,
        severity='warning',
    ),
    StagedFaultRule(
        test_shortid='ph',
        label='pH out of range',
        message=(
            'pH is {value:g}, outside the expected range for this stage. In the first '
            '48 hours, 3.2-4.6 is normal as must hasn\'t acidified yet; that narrows over '
            'the following 2 weeks to 3.0-3.6 once fermentation has settled - outside that, '
            'check for stuck fermentation or microbial contamination.'
        ),
        # Flat 3.2-4.6 through hour 48, linear taper to 3.0-3.6 by hour 384 (48h + 2wk), flat after.
        breakpoints=[(0, 3.2, 4.6), (48, 3.2, 4.6), (48 + 14 * 24, 3.0, 3.6)],
    ),
]


def get_rule_for(test_shortid):
    """First FAULT_RULES entry for a test type, so charts can draw the same threshold
    the flag logic uses instead of duplicating the number."""
    for rule in FAULT_RULES:
        if rule.test_shortid == test_shortid:
            return rule
    return None


def get_active_flags(batches=None):
    """
    Evaluate FAULT_RULES against each batch's latest reading of the relevant test type.

    Cheap enough (a handful of indexed .tests queries per batch) to call directly from
    a view - no Celery task needed for this. Pass an already-fetched queryset/list of
    batches to avoid re-querying when the caller already has one (e.g. views.index).
    """
    from apps.batchthis.models import Batch
    if batches is None:
        batches = Batch.objects.filter(active=True)
    flags = []
    for batch in batches:
        for rule in FAULT_RULES:
            flag = rule.evaluate(batch)
            if flag:
                flags.append(flag)
    return flags
