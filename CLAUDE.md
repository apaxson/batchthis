# Project Overview
- **Backend Framework:** Django 
- **Frontend Stack:** React.js (for Forms, performance, and Graphs/Data Visualization)
- **Task Queue:** Celery with Redis/RabbitMQ
- **Lint / Format:** Ruff (Python), ESLint/Prettier (JS)

## Project Application Goals
- **Batch This:** Application that tracks, manages, monitors, and guides the full lifecycle workflow of wine-making including recipe, fermentation, testing, treating, and bottling wine/mead.
- **User:** Application that manages preferences and logins for different user accounts and roles

## Error Handling
- NEVER swallow errors silently
- Always show user feedback for errors (Django messages, HTMX response headers)
- Log errors with proper context for debugging
- Create, at a minimum, the following log levels: DEBUG, ERROR, INFO for functions

## Key Commands
- **Run Django Server:** `python manage.py runserver`
- **Run Frontend Dev:** `npm run dev` or `vite` (depending on build tool)
- **Run Celery Worker:** `celery -A your_project_name worker --loglevel=info`
- **Run Celery Beat:** `celery -A your_project_name beat --loglevel=info`
- **Run Backend Tests:** `python manage.py test`
- **Create migrations:** `python manage.py makemigrations`
- **Apply migrations:** `python manage.py migrate`
- **Check migration status:** `python manage.py showmigrations`
- **Rollback last migration:** `python manage.py migrate <app_name> <previous_migration_number>`
- **Run Backend Tests (pytest):** `pytest` (pytest-django + factory-boy configured via `pytest.ini` at repo root; factories live in each app's `factories.py`, e.g. `apps/batchthis/factories.py`; test files live in each app's `tests/` directory, e.g. `apps/batchthis/tests/`)
  - **Run Tests for One App:** `pytest apps/<app_name>/tests/`
  - **Run a Single Test:** `pytest apps/<app_name>/tests/test_<name>.py::test_function_name`

## Project Structure
- `meadery/`: Project settings, celery setup, and root URL configurations.
- `apps/`: Feature-based modular Django apps.
- `frontend/`: React components, graph modules, and form handlers.
- `Folder Structure per App`
```
apps/<app_name>/
    ├── models.py        (Database models)
    ├── signals.py       (Django signal logic)
    ├── views/           (Python package for Django views and viewsets as admin.py, main.py, rpc.py, and APIViews in api.py)
    ├── serializers.py   (DRF Serializers)
    ├── apps.py          (Register signals here.  Metadata on application)
    ├── utils.py         (Misc helpers)
    ├── urls.py          (URL routing)
    ├── tests/           (application specific testing, e.g. test_<name>.py)
    ├── templates/       (application templates) 
    ├── fields.py        Custom Fields
```

## React components
- repeatable component for ModelChoiceField is react-select

## Cellar Ledger UI Redesign (Django template pages)
Server-rendered pages under `apps/batchthis/templates/batchthis/` are being migrated one at a time to the "Cellar Ledger" design system. When asked to redesign/update the look of a page, or convert it away from crispy-forms/topbar:
- **Design system files:** `apps/batchthis/static/batchthis/css/cellar-ledger.css` (tokens + all `cl-*` classes) and `apps/batchthis/static/batchthis/js/cellar-ledger.js` (shared interactive behavior — sidebar collapse, formset add/remove rows, mouse-following tooltips, the refractometer modal). Both are loaded globally via `base.html`, so new shared behavior belongs there, not duplicated per-page.
- **Verify which template is actually live before redesigning.** This app has dead/legacy template files still sitting in the tree (e.g. `addRecipe.html` is unreferenced by any view — the real template is `addRecipe2.html`) and duplicate URL `name=` entries in `urls.py` (e.g. `editRecipe` is defined twice; `reverse()` silently resolves to whichever is declared last). Grep the view's actual `render()` call rather than assuming from the filename.
- **Redesigned pages never** `{% include 'batchthis/topbar.html' %}` — `base.html`'s sidebar replaced it. Leaving it in on a still-legacy page renders a stale, duplicate top nav.
- **Forms:** drop crispy-forms entirely (no `{% load crispy_forms_tags %}`, no `|as_crispy_field`). Hand-roll each field as `.cl-field` (label + `{{ form.x }}` + `.cl-field-error`), inside `.cl-panel` > `<form class="cl-form">` > `.cl-form-grid` (3-col, `.cl-field--grow` to span a row), non-field errors in one `.cl-flag`, actions in `.cl-form-actions` (`.cl-btn--secondary` Cancel / `.cl-btn--primary` Save). Reference: `addBatch.html`.
- **Repeating formset rows** (add/remove a row, e.g. recipe fermentables/adjuncts/yeasts) use `.cl-formset-row`, not `.cl-form-grid` — reference `editYeasts.html`/`editFermentables.html`/`editAdjuncts.html`.
- **List/detail tables** use `.cl-section-head` + `.cl-table-scroll` + `.cl-ledger` — reference `recipe.html`. When the same rows are displayed on more than one page (e.g. `includes/recipe_additions.html`), reuse that exact table markup rather than inventing new markup for it.
- **Hover tooltips** on a name/label cell (showing a related model's notes) just need `class="... js-mouse-tooltip" data-toggle="tooltip" title="{{ obj.notes|default:'' }}"` — the show/follow-cursor/hide behavior is already wired up globally in `cellar-ledger.js`. Don't reimplement it per page.
- **A modal that needs to hand a computed value back to an arbitrary field on the calling page** should follow the existing pattern: one global modal defined in `base.html` (see `#refractometerModal`) + logic in `cellar-ledger.js`, triggered from any page with `data-toggle="modal" data-target="#theModal" data-target-field="#target_field_id"`. Don't build a one-off modal per page.
- **Before adding new CSS**, check `cellar-ledger.css` for an existing `.cl-*` class that already covers it.
- **After a redesign, verify live in a browser** (not just a template diff) — start the dev server, log in with a throwaway session (`SessionStore` + `SESSION_KEY`/`BACKEND_SESSION_KEY`/`HASH_SESSION_KEY`, since `ALLOWED_HOSTS` only permits `127.0.0.1`/`192.168.1.35`, not `localhost`), and exercise any JS behavior (AJAX cascades, tooltips, modals) with Playwright/screenshots, not just a page-load check.
- **Specific Gravity Display** Specific Gravity is always displayed with a decimal precision of 3.  Always pad with 0's to fulfil this.

## Code Style & Architecture Guidelines
- **Views:** Use class-based views for complex logic, function-based for simple endpoints.
- **Forms & Graphs:** Keep validation and rendering on the React client side. Backend views should strictly accept/return JSON payloads.
- **Celery Tasks:** Offload all heavy graph computations, long-running reports, and non-immediate data mutations to Celery. 
- **Task Naming:** Always explicitly name Celery tasks using a consistent domain pattern (e.g., `apps.reports.tasks.generate_graph_data`).
- **Timed Events/Tasks:** Use Celery for time-based events and task management using 5 minute intervals.
- **URLs:** namespaced per app, named with app_name:action-model pattern
- **Models:** TimeStampedModel base class for all models (adds created_at, updated_at)
- **API:** Use APIView for DRF
- **Serializers:** ModelSerializer with explicit fields (never fields = '__all__')
- **Units of Measurement:** Any measurement data should use batchthis.fields.DescriptiveQuantityField().  The default storage in models.py for the database should always convert to metric, but redisplay based on entered measurement.  See batchthis.models.Batch.size for an example.
  - **Volume form inputs:** every form field that takes a volume uses `batchthis.fields.VolumeField` (e.g. `size = VolumeField(label="Batch Size", placeholder="i.e. 6 gallons")`), never a plain `CharField`. It accepts text like "6 gallons" / "20 L", returns the Quantity as entered for a `DescriptiveQuantityField`, and rejects a bare number ("Units are required."), non-volume units, and zero/negative amounts. Pass `required=False` for optional volumes. References: `BatchAddForm.size`, `VesselForm.capacity`/`fill`.


## Constraints & Rules
- Do not run time-consuming logic inside the request-response cycle of a view; pass it to Celery.
- Always handle CORS carefully when frontend and backend environments are split (`django-cors-headers`).
- Write tests (TDD with pytest-django and Factory Boy) before implementing major feature plan stages.
- Explicitly trace all files impacted by a change (models, forms, URLs, views, and templates) before writing code.
- Always use Python type hints for public functions and service layers.
- When implementing a new feature against an existing test, do NOT fix a failed test function.  Inform.
- Use Django signals sparingly, prefer explicit service functions
- Use `select_related()` and `prefetch_related()` to avoid N+1 queries
- **NEVER write or edit migration files manually.** Always modify `models.py` first, then command Claude to run `python manage.py makemigrations` to let the Django framework auto-generate files.
- **Never commit unapplied migrations.** Always test migrations locally using `python manage.py migrate` before declaring a feature complete.
