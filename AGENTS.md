# SIGECOP

Sistema de gestión de contratistas y permisos: calcula si una persona o empresa contratista está habilitada para ingresar o trabajar en las instalaciones de un cliente. Django 5.2, DRF, HTMX, Celery + Redis y Tailwind.

**La fuente de verdad es `doc/constitution.md`.** Léela antes de cualquier cambio de diseño. Nada puede contradecirla en silencio.

## Comandos (PowerShell, desde la raíz)
- Python del proyecto: `env\Scripts\python.exe` (Python 3.12, settings `sigecop.settings.dev`)
- Pruebas: `env\Scripts\python.exe manage.py test` (una app: `... test apps.antecedentes`)
- Chequeo: `env\Scripts\python.exe manage.py check`
- Lint: `env\Scripts\ruff.exe check .`
- Seguridad: `env\Scripts\bandit.exe -r apps sigecop -ll` y `env\Scripts\pip-audit.exe -r requirements.txt`
- CSS: `npm run build:css` (o `npm run watch:css` mientras desarrollas)

Antes de dar algo por terminado, corre lo mismo que CI: `check`, `test`, `ruff check .` y `bandit`.

## Reglas no negociables (resumen de la constitución)
- Antecedentes, cumplimiento documental y habilitación son tres decisiones separadas. Solo `apps.accesos.services.calcular_habilitacion()` lee las tres, y no escribe nada.
- Un fallo técnico nunca es un resultado favorable. Nunca evadas un CAPTCHA ni controles antibots.
- Nada se sobrescribe: documentos, verificaciones y revisiones son inmutables. Renovar = crear una fila nueva.
- Los estados solo cambian a través del `services.py` de cada app (y de `transicionar()` donde hay máquina de estados), nunca desde vistas ni el admin.
- Toda mutación relevante llama a `apps.core.events.emitir_evento(...)`.
- Multi-tenant: filtra por organización de forma explícita en cada vista.
- Mínimo privilegio y cuatro ojos en antecedentes.
- Dependencias entre apps solo hacia atrás: core → identidad → empresas → personas → cumplimiento → ...

## Flujo de trabajo
- El trabajo grande se sigue en `odd/tasks/`. Revisa ahí antes de empezar una feature.
- Ramas por feature y PR a `main`. Nunca hagas push sin pedírmelo.
- Ruff: líneas de hasta 120 caracteres, comillas dobles.
