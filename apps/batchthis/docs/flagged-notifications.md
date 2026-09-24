# Flagged notifications

The "Flagged" panel on the Cellar Log dashboard surfaces batches whose most recent
test reading has drifted outside a safe range — e.g. free SO₂ dropping low enough
to risk oxidation. It's the first piece of the wine-fault troubleshooting goal in
the project's `CLAUDE.md`.

This doc covers how the feature is wired today, how to add or change fault rules,
and how to grow it into something bigger (per-style thresholds, Celery-driven
proactive notifications) without redoing the plumbing.

## How it works today

Three pieces:

1. **`apps/batchthis/lib/faults.py`** — a small rule engine. Each `FaultRule` names
   a `BatchTestType.shortid` (`'so2'`, `'ph'`, `'specific-gravity'`, `'yan'`, `'ta'`,
   `'temperature'` — see `apps/batchthis/migrations/0002_default_load.py`), a
   `minimum` and/or `maximum`, a severity, and a message template. `get_active_flags()`
   pulls each batch's *latest* reading of that test type and checks it against every
   rule.
2. **`views/main.py: index()`** — calls `get_active_flags(active_batches)` and puts
   the result in the `flags` context variable, reusing the `active_batches` queryset
   the view already fetched (no extra query for the batch list itself).
3. **`templates/batchthis/index.html`** — loops over `flags` and renders each as a
   `.cl-flag` block (styled in `static/batchthis/css/cellar-ledger.css`, driven by
   the `--brick` token). Empty state is a plain "Nothing flagged" message, not an
   error — a quiet dashboard is the expected common case.

A "flag" is a plain dict, not a model:

```python
{
    'batch': <Batch>,
    'test': <BatchTest>,      # the reading that tripped the rule
    'severity': 'warning',
    'label': 'Free SO₂ low',
    'message': 'Free SO₂ has fallen to 9 ppm, below the 10 ppm floor...',
}
```

Keep that shape if you extend `get_active_flags()` — the template only reads
`flag.batch`, `flag.message`, and (once you add more severities — see below)
`flag.severity`.

## Adding a fault rule

Add a `FaultRule` to the `FAULT_RULES` list in `faults.py`. Example — flag a pH
that's drifted too high for a mead sitting in secondary:

```python
FaultRule(
    test_shortid='ph',
    label='pH drifting high',
    message='pH has climbed to {value:g}. Above 4.0 raises the risk of microbial spoilage - consider a TA check.',
    maximum=4.0,
    severity='warning',
),
```

`{value}` and `{batch}` are available in `message` via `str.format`. Each rule
only queries `batch.tests.filter(type__shortid=...).order_by('-datetime').first()`
per active batch, so adding a handful of rules stays cheap enough to run inline
in the dashboard view — no Celery needed for this part. Per `CLAUDE.md`, Celery
is for heavy/long-running work; a threshold check on one row per batch isn't that.

**Units:** rules compare `test.chart_value` - each reading converted to its test
type's standard unit (sg, °F, ppm with 1 mg/L = 1 ppm, g/L, pH), per
`READING_SPECS` in models.py - so thresholds are always in those units no matter
what unit a reading was entered in. (Before 2026-09-24 rules compared the raw
`test.value` and assumed it was entered in the rule's unit.)

## Customizing severity and styling

Only `severity='warning'` (brick/red, via `--brick`) is styled today, since it's
the only case in use. To add a second severity (e.g. `'critical'` for something
that should stop a bottling run):

1. Add a token in `cellar-ledger.css` (`:root`), e.g. `--critical: #6B2320;` (a
   deeper red — keep it distinguishable from `--brick` at a glance, not just by
   the label next to it).
2. Add a `.cl-flag--critical` modifier class mirroring `.cl-flag` but keyed to
   the new token, and set it in `index.html` from `flag.severity`:
   `<div class="cl-flag{% if flag.severity == 'critical' %} cl-flag--critical{% endif %}">`.
3. Never rely on color alone to carry severity — keep the `label`/`message` text
   and the `!` mark (or a distinct mark per severity) so the distinction survives
   for colorblind users and in a table/CSV export.

## Growing this into per-style, DB-backed rules

Right now thresholds are one global list in Python — fine for a handful of rules
you, the brewer, control. If this needs to vary per `BatchCategory` /`BatchStyle`
(a cyser can tolerate different SO₂ ranges than a dry mead) or be editable without
a deploy, promote it to a model instead of hand-editing `faults.py`:

```python
class FaultRule(models.Model):
    class Severity(models.TextChoices):
        WARNING = 'warning', 'Warning'
        CRITICAL = 'critical', 'Critical'

    class Meta:
        ordering = ['test_type__name']
        verbose_name = 'fault rule'

    def __str__(self):
        return self.label

    test_type = models.ForeignKey(BatchTestType, on_delete=models.CASCADE, db_index=True)
    style = models.ForeignKey(BatchStyle, on_delete=models.CASCADE, null=True, blank=True)
    label = models.CharField(max_length=50)
    message = models.CharField(max_length=250)
    minimum = models.FloatField(null=True, blank=True)
    maximum = models.FloatField(null=True, blank=True)
    severity = models.CharField(max_length=10, choices=Severity.choices, default=Severity.WARNING)
```

That's a real schema change — per `CLAUDE.md`, run `makemigrations`, inspect the
migration before applying it, and commit the migration in the same commit as the
model change. `get_active_flags()` would then load `FaultRule.objects.filter(...)`
(optionally scoped by `batch.category.style`) instead of iterating the Python
list — the rest of the pipeline (view → context → template) doesn't change.

## Proactive notifications (Celery)

Today, flags are only computed when someone loads the dashboard. To actually
notify the brewer when a batch drifts out of range — rather than waiting for them
to check — add a Celery Beat periodic task, per the project's convention of using
Celery for timed notification events (see the existing TODO at
`apps/batchthis/models.py` near `BatchTest`, which flags this exact gap):

```python
# apps/batchthis/tasks.py
from celery import shared_task
from apps.batchthis.lib.faults import get_active_flags

@shared_task(name='apps.batchthis.tasks.check_batch_faults')
def check_batch_faults():
    flags = get_active_flags()
    for flag in flags:
        # send_notification(flag) - email/Slack/whatever the notification
        # system ends up being; see the models.py TODO for the same open question.
        pass
```

Schedule it in Celery Beat (e.g. every hour) so a fault gets caught even if
nobody opens the dashboard that day. The dashboard's inline `get_active_flags()`
call stays as-is either way — it's the "check right now, for this page load"
path; the Beat task is the "check periodically and push a notification" path.
They share the same rule engine on purpose.

## Rollout status

This pass (`design/cellar-ledger-redesign` branch) redesigned the app shell,
sidebar navigation, the dashboard (`index.html`), the batch detail page
(`batch.html`), and the "all readings" page (`batchGraphs.html`) to the Cellar
Ledger look, with the Flagged panel wired to real data on the dashboard (all
active batches) and the batch detail page (just that batch). The add/edit form
templates (`addBatch.html`, `addRecipe*.html`, `addTest.html`, etc.) still
render with the original SB Admin 2 styling — they inherit the new sidebar via
`base.html`, so navigation is consistent, but their content areas are unchanged.

`batchGraphs.html` now generates one instrument panel per test type the batch
actually has readings for (`views/main.py: batchGraphs()`, reusing
`_build_series()` from the batch detail view), instead of being limited to
whatever `Chart.min.js` canvas markup was hand-written per type. Each test
type's chart color/decimals/unit live in one place -
`views/main.py: CHART_STYLE` - so adding a 7th `BatchTestType` later needs a
one-line addition there, not new template markup.

The batch detail page now draws its own specific gravity / pH / free SO₂ charts
as hand-drawn SVG (`CellarLedger.renderChart` in `cellar-ledger.js`, fed by
`{{ chart|json_script:"..." }}` blocks) instead of the old Chart.js canvas, with
a hover crosshair/tooltip and a "view as table" fallback on each. The SO₂ chart
draws its threshold line from `get_rule_for('so2').minimum` in `faults.py`, so
the chart and the flag can't drift out of sync with each other.

**Bug found and fixed along the way (unrelated to the redesign):** the
`addGravityTest` post_save signal in `models.py` assigned a `pint.Quantity`
(`instance.startingGravity`) directly to `BatchTest.value`, a plain
`FloatField`. That raised on every new batch creation once a real `Quantity`
reached it — caught while building an isolated-test-DB check for this page,
not by hand. Fixed to use `.magnitude`.
