TODO-BatchStage
================
Plan for tracking a batch's workflow timeline (pitch -> fermentation ->
aging -> filtering -> bottling) so we can measure how long a batch spent
in each phase. Sketched 2026-09-18. NOT STARTED - no code changes made yet.
This file is the reference to pick back up from.


GOAL
----
* When a batch is made, log timestamped transitions between production
  stages (Pitch, Racking, Fine Filtering, Course Filtering, Sterile
  Filtering, plus whatever the final Bottling->Completed edge is named -
  see "CANONICAL WORKFLOW GRAPH" below).
* Be able to compute elapsed time between any two stages for a batch
  (e.g. time between pitch and bottling).
* Eventually (phase 2, not required for v1): let a Recipe declare an
  *expected* stage timeline to compare actual batches against - see the
  "RECIPE WORKFLOW (VESSEL CHAIN)" section below for the fleshed-out
  design (updated 2026-09-19, per Aaron).
* Phase 2 will leverage the timeline to identify next steps and alert notifications


WHAT ALREADY EXISTS (found while tracing this - don't re-invent these)
------------------------------------------------------------------
* `BatchStage` model already exists (apps/batchthis/models.py:67). Just a
  `name` field. Registered in admin.py, seeded with ZERO rows, referenced
  by nothing else in the codebase. This is the lookup table for the stage
  vocabulary - reuse/extend it, don't build a new parallel one.
* `batch_stages` list (models.py:35, PRI/SEC/TER/RAC/AGE) is dead code -
  never assigned to any field. It's the intended seed vocabulary for
  `BatchStage` rows, just never wired up. Delete it once BatchStage is
  seeded with real rows (extend with Pitch/Filtering/Bottling, which
  aren't in this list).
* Transitions: transitions are the action verbs between the stages.
  * _pitching_ - the initial action that moves to the 'Fermentation' stage
  * _racking_ - the action that moves from 'Fermentation' to 'Aging' stage
    * multiple rackings could happen as needed during a batch
  * _filtering_ - the action that happens after the 'Aging' stage
    * multiple filterings could happen as needed during a batch to be placed into a different aging tank that is 'clean'
    * 'Course Filtering' is a filter at 5micron to 10micron
    * 'Fine Filtering' is a filter at 1.5micron to 1micron
    * 'Sterile Filtering' is a filter at 0.5 micron meant for bottling
  * _bottling_ - the final action that happens after aging
  * SUPERSEDED by the diagram below (apps/batchthis/docs/WineWorkflow.png) -
    see "CANONICAL WORKFLOW GRAPH" for the authoritative shape and how it
    differs from this informal description (racking repeatability,
    Sterile Filtering's real role, the unlabeled final transition).
* `Batch.complete()` (models.py:378) sets enddate/active=False but is
  never called from any view. This is the hook for "batch reached its
  final stage."
* `Batch.transfer(src_vessel, dst_vessel)` (models.py:375) is a `pass`
  stub, clearly meant for the vessel move that happens on
  racking/aging transitions.
* `BatchTest` (dated + type FK + batch FK) and `BatchNote` (dated +
  notetype FK + batch FK) are the existing precedent for "append-only,
  timestamped, typed log entry against a batch." A stage-transition log
  is structurally the same shape - copy this pattern.
* `RecipeAdjunct.time_to_add` (minutes-since-batch-start) is the existing
  idiom for "offset from batch start" planning data - reusable later for
  the phase-2 recipe-side expected timeline.

CANONICAL WORKFLOW GRAPH (apps/batchthis/docs/WineWorkflow.png)
------------------------------------------------------------
Source-of-truth diagram, read 2026-09-19 (and re-read after Aaron updated
it same day to add "Sterile Filtering"). This is a real state diagram -
boxes are STATES, labeled arrows are TRANSITIONS - and it resolves the
informal "Transitions" note above into something concrete enough to
model directly:

    Start --Pitch--> Fermentation --Racking--> Aging --Sterile Filtering--> Bottling --(unlabeled)--> Completed
                                                  ^  |
                                     Fine Filtering|  |  (Aging -> Aging loop)
                                                  ^  |
                                   Course Filtering|  |  (Aging -> Aging loop)

States: Fermentation, Aging, Bottling, Completed. (`Start` is a
pseudostate, not a real batch state.)

Transitions (name: from_state -> to_state):
  * Pitch:             (Start) -> Fermentation
  * Racking:           Fermentation -> Aging
  * Fine Filtering:    Aging -> Aging          (loop - 1.5-1 micron)
  * Course Filtering:  Aging -> Aging          (loop - 5-10 micron)
  * Sterile Filtering: Aging -> Bottling       (0.5 micron - this is
                        what actually advances the batch out of Aging;
                        NOT just a bottling-technique attribute like I
                        originally guessed - the diagram draws it as its
                        own real transition)
  * (unlabeled):        Bottling -> Completed

This changed twice while sketching this doc, worth remembering:
  1. First version had no Sterile Filtering at all - Aging went straight
     to a "Bottled" terminal state via a transition literally labeled
     "Bottling".
  2. Second version renamed the terminal state "Bottled" -> "Completed",
     inserted a new real state "Bottling" between Aging and Completed,
     and gave the Aging -> Bottling edge the name "Sterile Filtering".
     The final Bottling -> Completed edge has NO label - every other
     arrow in the diagram is bold-labeled, this one isn't.

Discrepancies / open questions raised by the diagram (don't silently
resolve these - ask Aaron):
  [ ] The diagram draws Racking as a single one-directional edge
      (Fermentation -> Aging, happens once). The earlier informal note
      says "multiple rackings could happen as needed." Is Racking
      actually repeatable (e.g. Aging -> Aging, like the two Filtering
      transitions), and the diagram is just simplifying, or is Racking
      really a one-time event and the note was describing something else?
  [ ] Bottling -> Completed has no label. Intentional (nothing to log,
      just elapsed time until "done")? Or does it need a real transition
      name (e.g. "Capping", "Corking", "Sealing")?
  [ ] Is this diagram THE fixed canonical graph for every batch/recipe
      (same shape always, only the loop counts and vessel choices vary
      per batch), or is it one example and other recipes could have a
      genuinely different graph shape? This materially changes the
      recipe-workflow UI: if the graph is fixed, "add/change/reorder"
      really only means "how many Aging<->Filtering loop iterations and
      which vessel each time" - a much smaller feature than a generic
      graph/chain editor.

Design implication for BatchStage: since the diagram cleanly separates
STATES (boxes) from TRANSITIONS (labeled arrows), `BatchStage` should
hold the transitions and should record BOTH ends of the edge, not just
a name:
  - name         (existing)
  - shortid      (planned above)
  - from_state   (CharField choices, nullable - null means "from Start")
  - to_state     (CharField choices)
  - notes        (e.g. micron spec for the three filtering transitions)
This makes `Batch.current_state` mechanical to derive: it's just the
`to_state` of the most recent `BatchStageEvent.stage`. It also means the
model could (optionally, not required for v1) validate "you can't log
Racking unless the batch's current_state is Fermentation" - flagged as
an open decision, not a given, since this app generally avoids adding
validation for its own sake.

States themselves (Fermentation/Aging/Bottling/Completed) are few and
tied to the fixed graph shape, not open-ended user data like the other
"Type" lookup tables in this app (BatchTestType, BatchNoteType, ...) -
recommend a plain CharField `choices` for state values rather than a
full lookup model, unless the "different recipes, different graphs"
question above comes back "yes, graphs vary."


BUG FOUND ALONG THE WAY (separate from this feature, but blocks it)
---------------------------------------------------------------
* `Batch.startdate` is `models.DateTimeField(auto_now_add=True)`.
  `addBatch` view (views/main.py:349) already tries to set it from the
  form, but Django silently discards any assigned value on an
  auto_now_add field - it's always "now" at creation and can never be
  edited. This field also feeds the fault-checking engine's elapsed-time
  math (lib/faults.py:100-102) and recipe.html's batch ordering.
  If startdate is meant to double as the "Pitch" timestamp, this needs
  fixing first (auto_now_add=True -> default=timezone.now, editable).
  Confirm with Aaron before bundling this fix into the stage-tracking
  work vs. filing it separately.


MODEL CHANGES
-------------
1. Extend `BatchStage` (holds TRANSITIONS, not states - see "CANONICAL
   WORKFLOW GRAPH" above):
   - add `shortid` (SlugField, unique, auto-slug on save - same pattern
     as BatchTestType) so code can reference stages by stable id instead
     of matching on display name.
   - add `sort_order` (PositiveSmallIntegerField) for a defined sequence.
   - add `from_state` (CharField choices, blank=True - blank means "from
     Start") and `to_state` (CharField choices) per transition.
   - new data migration seeds, matching the diagram exactly:
       Pitch             (from: -,        to: Fermentation)
       Racking           (from: Fermentation, to: Aging)
       Fine Filtering    (from: Aging,    to: Aging)   # 1.5-1 micron
       Course Filtering  (from: Aging,    to: Aging)   # 5-10 micron
       Sterile Filtering (from: Aging,    to: Bottling) # 0.5 micron
     Plus whatever name is chosen for the unlabeled Bottling->Completed
     edge (open question above) - don't seed a guess, confirm with Aaron
     first.
     (Supersedes the dead `batch_stages` list, AND supersedes this file's
     own earlier seed list draft of "Pitch, Primary Fermentation,
     Secondary Fermentation, Racking, Tertiary/Aging, Filtering,
     Bottling" - that draft mixed states and transitions together before
     the diagram existed. Delete the dead `batch_stages` list once this
     is seeded.)

2. New `BatchStageEvent` model:
   - batch    = FK(Batch, on_delete=CASCADE, related_name='stage_events')
   - stage    = FK(BatchStage, on_delete=models.SET("_del"))  # matches
                every other lookup FK in this app
   - timestamp = DateTimeField(auto_now_add=False)  # user-entered, like
                BatchTest.datetime / BatchNote.date - people log these
                after the fact
   - vessel   = FK(Vessel, null=True, blank=True, on_delete=SET_NULL)
                # optional - ties into the Batch.transfer() stub for
                racking/aging vessel moves
   - notes    = CharField(max_length=250, blank=True)
   - Meta.ordering = ['timestamp']

3. Fix `Batch.startdate`: auto_now_add=True -> default=timezone.now,
   editable. (See bug note above - confirm scope with Aaron first.)

4. `Batch` convenience methods:
   - stage_durations() -> pairs consecutive stage_events into
     (stage, start, end, duration)
   - current_stage_event property
   - Skip a denormalized `current_stage` cache field unless a list page
     actually needs the perf - don't add it speculatively.

5. Wire up `Batch.complete()`: call it from the new stage-logging
   service function when the batch reaches the terminal state
   (Completed) - i.e. when the unlabeled Bottling->Completed transition
   is recorded, NOT when Sterile Filtering/Bottling is reached (Bottling
   is an intermediate state now, not the terminal one - see "CANONICAL
   WORKFLOW GRAPH" above).


OPEN DECISIONS (ask Aaron / decide before coding)
--------------------------------------------------
[ ] Reuse existing `BatchStage` vs. build a fresh lookup table.
    -> Recommend: reuse, it's already the right shape.
[ ] Auto-log "Pitch" on batch creation (mirrors the existing
    addGravityTest post_save signal that auto-creates a gravity
    BatchTest) vs. require an explicit first log entry.
    -> Signal approach matches existing precedent in this file, but
       CLAUDE.md's current convention prefers explicit service functions
       over new signals. Leaning toward explicit service-function call
       from the view, at the cost of one extra manual step per batch.
[ ] Add BatchStageEvent.vessel now vs. later.
    -> Recommend: add the nullable field now (cheap), don't wire
       automatic vessel-status swapping yet.
[ ] Fix Batch.startdate auto_now_add bug as part of this work vs. as its
    own separate fix/ticket.
[ ] Recipe-side expected workflow (vessel chain) - phase 2, not required
    for v1. Design fleshed out below in "RECIPE WORKFLOW (VESSEL CHAIN)".
[ ] vessel_role as free-text label vs. FK to a small lookup table of
    reusable role names ("Primary Fermenter", "Aging Vessel", ...).
    -> Recommend: free-text CharField for v1 (simplest); revisit as an
       FK only if roles need to be reused/reported on across recipes.
[ ] Reorder UI: drag-and-drop vs. simple up/down buttons on each row.
    -> Recommend: up/down buttons for v1 (no new drag library, less JS),
       drag-and-drop later if it's actually annoying to use.
[x] BatchStage: single flat lookup mixing stage-names and transition
    verbs, vs. splitting stage (state) from transition. RESOLVED by the
    WineWorkflow.png diagram (2026-09-19): they're genuinely different
    graph elements (boxes vs. arrows). BatchStage = transitions, with
    from_state/to_state fields; states are a small fixed CharField
    choices set, not their own lookup table. See "CANONICAL WORKFLOW
    GRAPH" above.
[ ] Racking repeatability: diagram shows it as one-time
    (Fermentation->Aging only), earlier note said it could repeat.
    Needs Aaron's call - see "CANONICAL WORKFLOW GRAPH" above.
[ ] Name (if any) for the unlabeled Bottling->Completed transition.
[ ] Is the WineWorkflow.png graph fixed/canonical for every batch and
    recipe, or just one example shape? Determines whether the phase-2
    recipe workflow needs a real generic graph editor or just a "how
    many Aging<->Filtering loops, which vessel each time" UI. See
    "CANONICAL WORKFLOW GRAPH" above.
[ ] Enforce the graph (e.g. refuse to log Racking unless current_state
    is Fermentation) vs. just record whatever the user logs and let the
    graph be descriptive/reporting-only.
    -> Recommend: descriptive-only for v1, consistent with this app's
       general "don't validate what doesn't need validating" approach;
       revisit if bad data actually becomes a problem.


RECIPE WORKFLOW (VESSEL CHAIN) - PHASE 2
-----------------------------------------
Per Aaron (2026-09-19): the recipe-level workflow is a chain of vessel
occupancy, linked by the transition/action that moves the batch from one
vessel into the next. Aaron's original example used placeholder verb
names ("fining", "bottle") before the WineWorkflow.png diagram existed;
superseded below by the diagram's real vocabulary (Pitch / Racking /
Fine Filtering / Course Filtering / Sterile Filtering):

    (pitch) -> FermentationVessel1 -> (racking) -> AgingVessel1
             -> (course filtering) -> AgingVessel2 -> (fine filtering)
             -> AgingVessel3 -> (sterile filtering) -> BottlingVessel1
             -> (?) -> Completed

Read as a sequence of steps, where each step names the vessel the batch
sits in and the action that moved it there. The Filtering segment
(Course/Fine, in any order, zero or more times per the canonical graph's
Aging->Aging loops) is the part of the chain that's genuinely variable
per batch - the recipe template can show a typical/expected pass count,
but the actual batch may loop through it a different number of times
(see BatchStageEvent.recipe_step tie-in below for how that's reconciled).
The chain needs to support add / change / reorder.

Model: `RecipeVesselStep` (ordered rows per recipe, no separate "edge"
model needed - a strictly linear chain, no branching, so the previous
row in sort order IS the "from" vessel and the current row is the "to"
vessel; no need to store both ends explicitly):
  - recipe      = FK(Recipe, on_delete=CASCADE, related_name='vessel_steps')
  - sort_order  = PositiveSmallIntegerField
  - stage       = FK(BatchStage, on_delete=SET("_del"))  # the transition
                  INTO this step - Pitch / Racking / Course Filtering /
                  Fine Filtering / Sterile Filtering / (unlabeled final)
                  - reuses the same BatchStage vocabulary as
                  BatchStageEvent (see main section above), so recipe
                  plan and actual batch log speak the same language.
  - vessel_role = CharField(max_length=50, blank=True)  # descriptive
                  slot label, e.g. "Fermentation Vessel 1", "Aging
                  Vessel 2" - NOT an FK to Vessel, since a recipe is a
                  reusable template, not tied to one physical vessel.
                  Blank on the terminal step (the final, currently-
                  unnamed Bottling->Completed row) to mean "batch leaves
                  vessel tracking here, done."
  - notes       = CharField(max_length=250, blank=True)
  - Meta.ordering = ['sort_order']

Worked example (as rows, matching the WineWorkflow.png chain above):
  1. stage=Pitch,             vessel_role="Fermentation Vessel 1"
  2. stage=Racking,           vessel_role="Aging Vessel 1"
  3. stage=Course Filtering,  vessel_role="Aging Vessel 2"
  4. stage=Fine Filtering,    vessel_role="Aging Vessel 3"
  5. stage=Sterile Filtering, vessel_role="Bottling Vessel 1"
  6. stage=<TBD - unlabeled Bottling->Completed edge>, vessel_role=""
     (terminal - no vessel after; batch is Completed)
Row 3/4 are the repeatable Aging<->Filtering loop - a real recipe might
have more or fewer of these, and in either order (Course then Fine, or
Fine then Course, or just one of them repeated) - they're shown once
each here only as an example.

Tie-in to the v1 BatchStageEvent design (main section above): when an
actual batch logs a BatchStageEvent, add an optional nullable FK
`recipe_step = FK(RecipeVesselStep, null=True, blank=True,
on_delete=SET_NULL)` on BatchStageEvent, so an actual logged event can
point back at which planned step it fulfilled. That's what makes
expected-vs-actual comparison possible later (e.g. "recipe said Racking
happens ~day 7, this batch did it on day 9"). Optional for v1 of the
vessel-chain feature itself, but cheap to add at the same time.

BatchStage extension needed for this to work: all five named transitions
(Pitch, Racking, Fine Filtering, Course Filtering, Sterile Filtering) are
already in the v1 seed list above per the WineWorkflow.png diagram - no
schema change needed here, just data. The one gap is the unlabeled
Bottling->Completed edge, which still needs a name before it can be
seeded (see open questions in "CANONICAL WORKFLOW GRAPH" above).

UI (add / change / reorder), Cellar Ledger conventions:
  - New recipe sub-page `editVesselSteps.html`, following the exact
    formset pattern in editFermentables.html / editAdjuncts.html /
    editYeasts.html (.cl-formset-row, add-row button wired through
    CellarLedger.initFormsetAdd in cellar-ledger.js).
  - NEW shared behavior needed (doesn't exist yet): move-up/move-down
    reordering for formset rows. None of the existing formset pages
    reorder rows today (fermentables/adjuncts/yeasts have no inherent
    sequence). Add a `CellarLedger.initFormsetReorder()` helper to
    cellar-ledger.js (renumbers hidden `sort_order` inputs and re-orders
    row DOM nodes on click) rather than writing page-specific JS, per
    the "new shared behavior belongs in cellar-ledger.js" rule.
  - Link from recipe.html into this sub-page, same as the existing
    "fermentables / adjuncts / yeasts" edit links.

Files impacted (phase 2, additive to the v1 list below):
  * apps/batchthis/models.py - add RecipeVesselStep; add
    BatchStageEvent.recipe_step FK (nullable)
  * apps/batchthis/migrations/ - new migration(s)
  * apps/batchthis/admin.py - register RecipeVesselStep
  * apps/batchthis/forms.py - RecipeVesselStepForm (ModelForm) +
    inlineformset_factory, mirroring the Fermentable/Adjunct/Yeast
    formsets already in this file
  * apps/batchthis/views/main.py - editVesselSteps view, mirroring
    editFermentables/editAdjuncts/editYeasts
  * apps/batchthis/urls.py - route for editVesselSteps
  * apps/batchthis/templates/batchthis/editVesselSteps.html (new)
  * apps/batchthis/templates/batchthis/includes/_vessel_step_form_row.html (new)
  * apps/batchthis/templates/batchthis/recipe.html - link to the new
    sub-page; optionally render the chain read-only (e.g. a simple
    .cl-ledger table or an inline arrow diagram)
  * apps/batchthis/static/batchthis/js/cellar-ledger.js -
    CellarLedger.initFormsetReorder() helper (new shared behavior)
  * apps/batchthis/factories.py - RecipeVesselStepFactory
  * apps/batchthis/tests/test_recipe_vessel_steps.py (new) - add/edit/
    reorder steps, terminal step (blank vessel_role) handling


FILES IMPACTED (v1 scope - no recipe-plan / phase 2 items)
------------------------------------------------------------
* apps/batchthis/models.py
    - extend BatchStage (shortid, sort_order, from_state, to_state)
    - add BatchStageEvent
    - fix Batch.startdate
    - add Batch.stage_durations() / current_stage_event
    - delete dead `batch_stages` list once BatchStage is seeded
* apps/batchthis/migrations/
    - new migration(s) via `python manage.py makemigrations`
      (schema change + seed-data RunPython, like 0002_default_load.py)
    - NEVER hand-write migration files - see CLAUDE.md
* apps/batchthis/admin.py
    - register BatchStageEvent (BatchStage is already registered)
* apps/batchthis/forms.py
    - new BatchStageForm (ModelForm on BatchStageEvent, reuse the
      existing DateTimeWidget already defined at the top of this file)
      matching the shape of BatchTestForm / BatchNoteForm
* apps/batchthis/views/main.py
    - new batchStage(request, pk=None) view mirroring batchTest/batchNote
    - new log_stage_event() service function (calls batch.complete() on
      terminal stage) - per CLAUDE.md, prefer this over a signal
* apps/batchthis/urls.py
    - addStage / addDetailStage routes, mirroring addTest/addDetailTest
* apps/batchthis/templates/batchthis/batch.html
    - "Add stage" link in .cl-batch-actions
    - new "Stage timeline" .cl-panel using .cl-ledger
      (Stage / Timestamp / Elapsed / Vessel / Notes)
* apps/batchthis/templates/batchthis/addBatchStage.html (new)
    - Cellar Ledger form page, follow addBatch.html as the reference
* apps/batchthis/factories.py
    - BatchFactory (doesn't exist yet - batches are currently only built
      through the addBatch view in tests, never a factory)
    - BatchStageFactory
    - BatchStageEventFactory
* apps/batchthis/tests/test_batch_stage_events.py (new)
    - stage logging via the service function/view
    - duration calculation across a sequence of events
    - terminal-stage completion (enddate/active set via complete())
    - regression test for the startdate fix (create a batch with a
      specific past startdate via the view, assert it's persisted
      exactly, not overridden to "now")


SUGGESTED ORDER OF WORK (TDD - write the test for each step first)
--------------------------------------------------------------
1. Fix Batch.startdate bug + regression test (small, isolated, unblocks
   everything else that treats startdate as the pitch anchor).
2. Extend BatchStage (shortid, sort_order) + seed migration + BatchFactory
   + BatchStageFactory.
3. BatchStageEvent model + migration + BatchStageEventFactory +
   stage_durations()/current_stage_event tests.
4. log_stage_event() service function + terminal-stage completion test.
5. BatchStageForm + batchStage view + urls.
6. batch.html "Stage timeline" panel + addBatchStage.html template.
--- phase 2 (vessel-chain recipe workflow, see section above) ---
7. RecipeVesselStep model + migration + RecipeVesselStepFactory + tests
   (add/edit/reorder, terminal blank-vessel_role step).
8. CellarLedger.initFormsetReorder() shared JS helper.
9. RecipeVesselStepForm + inline formset + editVesselSteps view/urls.
10. editVesselSteps.html + _vessel_step_form_row.html templates; link
    from recipe.html.
11. BatchStageEvent.recipe_step FK + expected-vs-actual comparison UI.
