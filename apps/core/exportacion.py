"""
Spreadsheet export helpers (RF-063): text written by users is never exported as a formula.

A cell such as `=HYPERLINK(...)` would run when the exported file is opened (CSV/formula injection).
XLSX cells are forced to the string type, so the value is kept as written; CSV values that a
spreadsheet would read as a formula get a leading apostrophe (OWASP recommendation).
"""

PREFIJOS_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def escribir_filas_xlsx(hoja, filas, desde_fila=2):
    """
    Writes rows below the header keeping every text cell as plain text.

    Rows are addressed by number: `hoja.max_row` scans every cell, so using it per row is quadratic.
    """
    for numero, valores in enumerate(filas, start=desde_fila):
        for columna, valor in enumerate(valores, start=1):
            celda = hoja.cell(row=numero, column=columna, value=valor)
            if celda.data_type == "f":
                celda.data_type = "s"


def valor_csv(valor):
    if isinstance(valor, str) and valor.startswith(PREFIJOS_FORMULA):
        return f"'{valor}"
    return valor
