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

## Tasks

- T1 — S1-S4, S6 — permission matrix module + deny-by-default mixin in every internal view + nav filtering + tests — worker — pending
- T2 — S1 — internal users screen: invite by email (72 h activation), assign role and scope, deactivate — pending
- T3 — S1, S5 — gestor scope by empresa via AlcanceAsignado (needs director P2) — blocked
- T4 — entry permits require human approval (needs director P9) — blocked
- T5 — schedule daily jobs (Celery beat) + transactional emails — pending
- T6..T14 — see plan in `odd/diagnostico-sigecop.md` §6 — pending

## Log

- L1 — User request (verbatim, abridged to the parts this feature tracks; full text in the session): "Actúa como líder técnico, arquitecto de software, analista funcional y desarrollador sénior. Tu misión es ayudarme a terminar SIGECOP, un sistema que debe conservar las funcionalidades del sistema anterior y mejorarlas con una interfaz moderna, automatizaciones útiles y procesos más eficientes. [...] Presta especial atención al registro de empresas y usuarios, al envío de credenciales por correo y a la asignación de permisos. [...] No inventes reglas de negocio ni habilites permisos por defecto sin justificación. [...] Plan de trabajo de 14 días [...] P0: bloqueantes y necesarias para entregar. P1: funcionalidades importantes para el funcionamiento. P2: mejoras que pueden esperar. [...] No afirmes que algo está terminado si no lo has comprobado. No sobrescribas configuraciones, datos ni funcionalidades críticas sin necesidad. Mantén los secretos fuera del código y no alteres entornos de producción sin autorización. [...] Después de entregar este diagnóstico, continúa con las tareas seguras e independientes sin esperar a que se resuelvan todas las dudas."
- L2 — Evidence (audit 27/10): only `apps/antecedentes` checks role codes; every other internal view filters by `roles_asignados.filter(activo=True)` org ids only, so any role (including `seguridad_garita`) can create companies, review documents and request entries. `accesos/services.py:81` auto-approves a permit when the person is enabled. No videos in `doc/` yet; 17 screenshots analysed. Baseline: 352 tests OK on `main` 1faa2f4.
