from django.conf import settings
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.urls import Resolver404, resolve, reverse

# Rutas fuera del portal que una cuenta de portal sí puede usar.
_URLS_PERMITIDAS = {
    "identidad:login",
    "identidad:logout",
    "identidad:activar_cuenta",
}


class AccesoPortalMiddleware:
    """
    S6: denegación por defecto para cuentas de portal. Solo alcanzan el
    namespace `portal`, login/logout/activación y los estáticos; todo lo
    demás (listas internas, admin, API, media, rutas desconocidas) da 403.
    Debe ir después de la autenticación. El personal interno y los anónimos
    pasan sin cambios.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        usuario = request.user
        if usuario.is_authenticated and usuario.es_cuenta_portal:
            denegada = self._denegar(request)
            if denegada is not None:
                return denegada
        return self.get_response(request)

    def _denegar(self, request):
        ruta = request.path_info
        static_url = "/" + settings.STATIC_URL.lstrip("/")
        if ruta.startswith(static_url):
            return None
        try:
            coincidencia = resolve(ruta)
        except Resolver404:
            return HttpResponseForbidden("Acceso denegado.")

        if coincidencia.namespace == "portal" or coincidencia.view_name in _URLS_PERMITIDAS:
            return None
        if coincidencia.view_name == "identidad:panel":
            return HttpResponseRedirect(reverse("portal:inicio"))
        return HttpResponseForbidden("Acceso denegado.")
