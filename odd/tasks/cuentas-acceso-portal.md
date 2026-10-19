# Cuentas de acceso y portal para empresas y personas

Feature: when an EmpresaContratista or Persona is created, provision its login account and email it a one-time activation link.
Branch: `feat/cuentas-acceso-portal`. Read until `## Log` before working on any task.

## Specs

- S1 — Account on creation: "quiero q al crear una empresa o un usuario se le envíe por correo el usuario y contraseña, para la empresa es el ruc y una cntraseña generada aleatoriamente y para la persona la cedula es el usuario". Username = `EmpresaContratista.ruc` for companies and `Persona.numero_documento` for persons; the email goes to the entity's `contacto_email`.
- S2 — Activation link instead of an emailed password (L2, Q4 "Enlace de activación"): no password travels by email; the email carries the username and a one-time link valid for 72 h where the user defines their own password. This replaces "al intentar iniciar sesion por primera vez deberan cambiarla": the account cannot log in until the password is defined through the link.
- S3 — Login with RUC/cédula: contractor accounts log in with their username; internal staff keep logging in with their email.
- S4 — Company portal (L2, Q1 "Portal de solo lectura"): "Mi empresa: datos, estado de cumplimiento y motivo. Mi personal: personas asignadas con su estado. Mis contratos. No ve nada de otras empresas."
- S5 — Person portal (L2, Q2 "Mi perfil + espacio para el pase QR"): "Sus datos, su estado documental y las empresas y cargos en los que está asignada" plus a card "Mi pase de ingreso" that says "Disponible cuando se apruebe tu ingreso".
- S6 — Isolation: portal accounts can only reach the portal (default deny); they never see internal lists, admin or API of their client organization.
- S7 — Reuse: "al tener flujos parecidos pueds encapsular y reutilizar funcionalidades como mixins por ejemplo". Company and person flows share one account mixin, one provisioning service and shared view mixins.
- S8 — Same RUC/cédula in another organization reuses the existing account (adds access, no second account).
- S9 — Staff can (re)send the access link from the company/person list (expired link, changed email).
- S10 — Tests: "en cada modulo haz las pruebas con test.py para cada app del proyecto".
- S11 — Sender configuration (L2, Q3): "eso lo configuraremos luego desde el panel administrativo, agregalo como tarea para el final". SMTP sender editable from the admin panel.
- S12 — Contact email is required when creating a company or person from the UI (the account needs a recipient).

## Tasks

- T1 — S1-S3, S7, S8, S10 — identidad: `nombre_usuario`/`tipo_cuenta`, `CuentaAccesoMixin`, provisioning service, activation token + view, login by username, tests — inline — done, commit: ef7f4a1
- T2 — S1, S7, S9, S10, S12 — empresas + personas: link accounts, provision on create, sync username on edit, "Enviar acceso" modal mixin, tests — inline — done, commit: ece0d6a
- T3 — S4-S6, S10 — portal app: middleware default deny, company and person views/templates, tests — worker (context backstop) — done, commit: bba1887
- T4 — S11, S10 — core: SMTP sender configuration editable from admin, tests — inline — done, commit: cdba9fa
- T5 — all — verification, constitution update — gentle-ai-verify + inline fixes — done, commit: see L5

## Log

- L1 — User request (verbatim): "perfecto entiendes que el proyecto es de contratos, vamos a ir desarrollando modulos paso a paso, primero terminemos el modulo de usuarios y empresa, quiero q al crear una empresa o un usuario se le envíe por correo el usuario y contraseña, para la empresa es el ruc y una cntraseña generada aleatoriamente y para la persona la cedula es el usuario y la contraseña también la generamos aleatoriamente, obviamente para ambos casos al intentar iniciar sesion por primera vez deberan cambiarla, dime q verán ambos cuando inicien sesion, al tener flujos parecidos pueds encapsular y reutilizar funcionalidades como mixins por ejemplo, armalo perfectamente, y en cada modulo haz las pruebas con test.py para cada app del proyecto"
- L2 — User decisions: Q1 company portal "Portal de solo lectura (Recomendado)"; Q2 person "Mi perfil + espacio para el pase QR"; Q3 "(custom) al referirte al correo de contacto es el que envía los correos?, eso lo configuraremos luego desde el panel administrativo, agregalo como tarea para el final"; Q4 security "Enlace de activación".
- L3 — Decisions: Q3 clarified as recipient (`contacto_email`) vs sender (SMTP, T4). Required in UI forms on create; service layer still accepts no email (existing callers) and then creates no account, staff sends access later (S9). Baseline suite before work: 36 tests OK.
- L4 — T1+T2 done: full suite 88 tests OK. Delivery slices: PR slice 1 = ef7f4a1 (identidad, ~900 lines) + ece0d6a (empresas/personas, ~650 lines); oversized for one review, recommend chained PRs per commit. T3 onward is slice 2.
- L5 — Independent verify: S1/S2/S6/S10/S12 pass; found D1 (high: shared account email takeover via enviar_acceso), D2 (shared titular edit blocked), D3 (document shaped like an email shadows staff login). Fixed with regression tests; full suite 124 OK, ruff clean. Constitution updated locally (doc/ is gitignored). Advisories left open: SMTP password plaintext in DB; email-uniqueness race -> IntegrityError 500; second active SMTP config -> IntegrityError instead of form error.
