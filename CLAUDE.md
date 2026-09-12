# Project Overview
- **Backend Framework:** Django (Function-Based Views)
- **Frontend Stack:** React.js (for Forms and Graphs/Data Visualization)
- **Task Queue:** Celery with Redis/RabbitMQ
- **Lint / Format:** Ruff (Python), ESLint/Prettier (JS)

## Project Intent and Goal
This project is meant to track, graph, test, and monitor multiple wine batches in various stages for a wine-making business, mostly for Mead and Wine with the occassional beer and cider from recipe and fermentation to bottling.
Leveraging known organic chemistry formulae reactions and wine faults, identify and troubleshoot wine flaws.  Track for future reference in workflows as to not be repeated.

## Models
- Use `models.TextChoices` for enum fields
- Add `__str__`, `Meta.ordering`, and `Meta.verbose_name` on all models
- Use `update_fields` in `.save()` calls to avoid overwriting concurrent changes
- Index frequently queried fields with `db_index=True` or `Meta.indexes`

## Key Commands
- **Run Django Server:** `python manage.py runserver`
- **Run Frontend Dev:** `npm run dev` or `vite` (depending on build tool)
- **Run Celery Worker:** `celery -A your_project_name worker --loglevel=info`
- **Run Celery Beat:** `celery -A your_project_name beat --loglevel=info`
- **Run Backend Tests:** `python manage.py test`

## Project Structure
- `apps/`: Feature-based modular Django apps.
- `apps/<appname>/frontend/`: React components, graph modules, and form handlers.
- `apps/<appname>/views/rpc.py`: Backend REST endpoint returning JSON and validating authentication for forms and react.js 

## Code Style & Architecture Guidelines
- **Views:** Prefer function-Based Views (FBVs). Decorate API endpoints with `@api_view` if using DRF or return `JsonResponse`.
- **Forms & Graphs:** Keep validation and rendering on the React client side. Backend views should strictly accept/return JSON payloads.
- **Form Modals:** Use react.js and backend RPC/API endpoints to create modal forms as form field helpers.
- **Celery Tasks:** Offload all heavy graph computations, long-running reports, and non-immediate data mutations to Celery. Celery is to manage timed-events for notifications and task management including logfile rotations.
- **Task Naming:** Always explicitly name Celery tasks using a consistent domain pattern (e.g., `apps.reports.tasks.generate_graph_data`).
- **Logging:** Logs are to be sent to log/<appname>/ and globally configured in meadery/settings.py.  Development should default to `DEBUG` while production should default to `ERROR`

## Constraints & Rules
- Do not run time-consuming logic inside the request-response cycle of a view; pass it to Celery.
- Always handle CORS carefully when frontend and backend environments are split (`django-cors-headers`).
- When modifying models, immediately generate migrations using `makemigrations` and inspect them before applying.
- Do not modify migration files after they have been applied
- Always include migrations in the same commit as model changes
- Maintain consistent look and feel UI with common CSS across page templates.
