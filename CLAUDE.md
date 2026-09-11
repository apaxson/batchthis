# Project Overview
- **Backend Framework:** Django (Function-Based Views)
- **Frontend Stack:** React.js (for Forms and Graphs/Data Visualization)
- **Task Queue:** Celery with Redis/RabbitMQ
- **Lint / Format:** Ruff (Python), ESLint/Prettier (JS)

## Key Commands
- **Run Django Server:** `python manage.py runserver`
- **Run Frontend Dev:** `npm run dev` or `vite` (depending on build tool)
- **Run Celery Worker:** `celery -A your_project_name worker --loglevel=info`
- **Run Celery Beat:** `celery -A your_project_name beat --loglevel=info`
- **Run Backend Tests:** `python manage.py test`

## Project Structure
- `config/`: Project settings, celery setup, and root URL configurations.
- `apps/`: Feature-based modular Django apps.
- `frontend/`: React components, graph modules, and form handlers.

## Code Style & Architecture Guidelines
- **Views:** Stick strictly to Function-Based Views (FBVs). Decorate API endpoints with `@api_view` if using DRF or return `JsonResponse`.
- **Forms & Graphs:** Keep validation and rendering on the React client side. Backend views should strictly accept/return JSON payloads.
- **Celery Tasks:** Offload all heavy graph computations, long-running reports, and non-immediate data mutations to Celery. 
- **Task Naming:** Always explicitly name Celery tasks using a consistent domain pattern (e.g., `apps.reports.tasks.generate_graph_data`).

## Constraints & Rules
- Do not run time-consuming logic inside the request-response cycle of a view; pass it to Celery.
- Always handle CORS carefully when frontend and backend environments are split (`django-cors-headers`).
