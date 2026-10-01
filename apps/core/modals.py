"""
Helpers para el patrón de modal HTMX: la vista devuelve un fragmento de
HTML que se inyecta en <div id="sc-modal"> (definido en base.html).
Si el formulario es válido, se responde con la cabecera HX-Redirect para
que el navegador vaya a la lista (ya con el mensaje de éxito visible).
"""

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import render


def render_modal(request, *, form, titulo: str, accion_url: str, boton: str = "Guardar"):
    return render(
        request,
        "partials/_modal_formulario.html",
        {"form": form, "titulo": titulo, "accion_url": accion_url, "boton": boton},
    )


def exito_htmx(request, *, mensaje: str, url: str):
    messages.success(request, mensaje)
    return HttpResponse("", headers={"HX-Redirect": url})
