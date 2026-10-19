# fix(antecedentes): bound PDF parsing and close verification findings

Rama: `pr/b8-antecedentes-correcciones` → base `pr/b7-antecedentes-vigencia`

## Chain Context

| Campo | Valor |
|-------|-------|
| Cadena | SIGECOP: cuentas de portal + antecedentes penales |
| Tracker PR | `feat/sigecop-contratos` → `main` (borrador, no fusionar) |
| Posición | 12 de 13 |
| Base | `pr/b7-antecedentes-vigencia` |
| Depende de | pr/b7-antecedentes-vigencia |
| Siguiente | Certificado real (B9). |

### Chain Overview

```text
main
 └── feat/sigecop-contratos  (tracker)
    1. pr/a1-cuentas-identidad
    2. pr/a2-cuentas-alta
    3. pr/a3-portal-lectura
    4. pr/a4-smtp-cuentas-compartidas
    5. pr/b1-antecedentes-estados
    6. pr/b2-antecedentes-autorizacion
    7. pr/b3-antecedentes-roles
    8. pr/b4-antecedentes-consulta-asistida
    9. pr/b5-antecedentes-pdf
   10. pr/b6-antecedentes-api-ui
   11. pr/b7-antecedentes-vigencia
📍 12. pr/b8-antecedentes-correcciones
   13. pr/b9-antecedentes-certificado-real
```

### Scope
- Incluye: Correcciones de la verificación independiente: análisis del PDF en proceso aparte con tiempo límite (pypdf 6.21), filtro de contenido activo, cuatro ojos por documento, 404 sin rol, API estricta. Revisión nativa: aprobada.
- Excluye: lo de los demás tramos; Función Judicial, firma criptográfica y cifrado de campos (T9).

### Verificación
- `env/Scripts/python.exe manage.py test` y `manage.py makemigrations --check --dry-run` en esta rama.
- Detalle y evidencia: `odd/tasks/cuentas-acceso-portal.md` y `odd/tasks/antecedentes.md`.
