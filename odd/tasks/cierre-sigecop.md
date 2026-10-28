# Feature: cierre-sigecop (14-day completion plan)

Finish SIGECOP keeping the previous system's functionality. Diagnosis and plan: `odd/diagnostico-sigecop.md`; open director questions: `odd/preguntas-director.md`.
Branch: `feat/cierre-sigecop`. Read until `## Log` before working.

## Specs

- S1 — Least privilege everywhere (L1): "Presta especial atención al registro de empresas y usuarios, al envío de credenciales por correo y a la asignación de permisos." "No inventes reglas de negocio ni habilites permisos por defecto sin justificación." Requirements role table: "Mínimo privilegio. Cada rol ve únicamente los datos necesarios para cumplir su función." "Seguridad o garita — Consulta habilitación y registra accesos. — No abre certificados de antecedentes ni expedientes sensibles." "AC-002 Un gestor no puede ver ni modificar otra empresa contratista." "AC-003 Garita ve habilitación y razón operativa sin abrir antecedentes."
- S2 — Role → action matrix is exactly the table in `odd/preguntas-director.md` P5 (derived only from the requirement role descriptions). Anything not in the table is denied. It lives in ONE module so a director answer changes one line.
- S3 — Deny by default: every internal view (empresas, personas, cumplimiento, accesos, trabajos, core alertas/export, identidad panel) checks the action for the user's organizations; without the role the response is HTTP 403 and nothing is written (no rows, no events). Multi-tenant filtering stays explicit (constitution §2.6). Antecedentes keeps its own `permisos.py` rules unchanged. Portal accounts keep `AccesoPortalMiddleware` unchanged.
- S4 — Navigation shows only the entries the user may use; hiding is cosmetic, the 403 is the real control.
- S5 — Gestor scope "solo sus empresas" (AlcanceAsignado) is NOT part of T1; T1 applies org-wide and documents the gap (T3).
- S6 — Tests per role through the HTTP interface (status code, and that a denied POST leaves stored data and event counts unchanged); existing behavior for allowed roles keeps passing.

- S7 — Internal users (T2): only `gestionar_usuarios` (admin_cliente) lists, invites, resends and deactivates internal users of its organizations. Assignable roles are exactly the matrix roles (`ROLES_INTERNOS`), never portal or platform roles. A new email gets an INTERNA account with no usable password and the 72 h activation link; an existing internal user only gets the role plus a notice without link; a portal account's email is refused. Deactivation requires a motive (RN-004) and nobody can deactivate their own admin_cliente role. Scope by installation is NOT offered until T3 enforces it (showing it would promise a control that does not exist).

## Tasks

- T1 — S1-S4, S6 — permission matrix module + deny-by-default mixin in every internal view + nav filtering + tests — worker (crashed) + inline — done, commit faca434 (see L3)
- T2 — S1, S7 — internal users screen: invite by email (72 h activation), assign role, resend, deactivate with motive — inline — done, commit: see L4
- T3 — S1, S5 — gestor scope by empresa via AlcanceAsignado (needs director P2) — blocked
- T4 — entry permits require human approval (needs director P9) — blocked
- T5 — schedule daily jobs (Celery beat) + transactional emails — pending
- T6..T14 — see plan in `odd/diagnostico-sigecop.md` §6 — pending

## Log

- L1 — User request (verbatim, abridged to the parts this feature tracks; full text in the session): "Actúa como líder técnico, arquitecto de software, analista funcional y desarrollador sénior. Tu misión es ayudarme a terminar SIGECOP, un sistema que debe conservar las funcionalidades del sistema anterior y mejorarlas con una interfaz moderna, automatizaciones útiles y procesos más eficientes. [...] Presta especial atención al registro de empresas y usuarios, al envío de credenciales por correo y a la asignación de permisos. [...] No inventes reglas de negocio ni habilites permisos por defecto sin justificación. [...] Plan de trabajo de 14 días [...] P0: bloqueantes y necesarias para entregar. P1: funcionalidades importantes para el funcionamiento. P2: mejoras que pueden esperar. [...] No afirmes que algo está terminado si no lo has comprobado. No sobrescribas configuraciones, datos ni funcionalidades críticas sin necesidad. Mantén los secretos fuera del código y no alteres entornos de producción sin autorización. [...] Después de entregar este diagnóstico, continúa con las tareas seguras e independientes sin esperar a que se resuelvan todas las dudas."
- L2 — Evidence (audit 27/10): only `apps/antecedentes` checks role codes; every other internal view filters by `roles_asignados.filter(activo=True)` org ids only, so any role (including `seguridad_garita`) can create companies, review documents and request entries. `accesos/services.py:81` auto-approves a permit when the person is enabled. No videos in `doc/` yet; 17 screenshots analysed. Baseline: 352 tests OK on `main` 1faa2f4.
- L3 — T1: worker wrote the matrix and views then crashed (not retried); parent reviewed and finished. GGA rejected the first commit because `core/views.py` imported `identidad` (forward dependency); alert views moved to `apps/identidad/views_alertas.py` (same paths, namespace `identidad:`), `core/views.py` and `core/urls.py` left empty pending user confirmation to delete them. Verified on faca434: check OK, 396 tests OK, ruff and bandit clean.
- L4 — T2 findings: the old unused `services.invitar_usuario` created users with `is_active=False` and activation never set it back, so an invited internal user could never log in; replaced. Access email printed `None` as username for internal accounts; now falls back to the email.
