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
- **Run Backend Tests (pytest):** `pytest` (pytest-django + factory-boy configured via `pytest.ini` at repo root; factories live in each app's `factories.py`, e.g. `apps/batchthis/factories.py`)
  - **Run Tests for One App:** `pytest apps/<app_name>/`                  
  - **Run a Single Test:** `pytest apps/<app_name>/test_<name>.py::test_function_name`     
- 
## Project Structure
- `meadery/`: Project settings, celery setup, and root URL configurations.
- `apps/`: Feature-based modular Django apps.
- `frontend/`: React components, graph modules, and form handlers.
- `apps/<app_name>/`
    ├── models.py        (Database models)
    ├── signals.py       (Django signal logic)
    ├── views/           (Python package for Django views and viewsets as admin.py, main.py, rpc.py, and APIViews in api.py)
    ├── serializers.py   (DRF Serializers)
    ├── apps.py          (Register signals here.  Metadata on application)
    ├── utils.py         (Misc helpers)
    ├── urls.py          (URL routing)
    ├── tests.py         (application specific testing)
    ├── templates/       (application templates) 

## React components
- repeatable component for ModelChoiceField is react-select
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
