from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class NombreUsuarioOCorreoBackend(ModelBackend):
    """
    S3: las cuentas de portal entran con su RUC/cédula (`nombre_usuario`);
    el personal interno sigue entrando con su correo (USERNAME_FIELD).
    Hereda de ModelBackend los permisos y `user_can_authenticate`.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        Usuario = get_user_model()
        if username is None:
            username = kwargs.get(Usuario.USERNAME_FIELD)
        if not username or password is None:
            return None

        identificador = username.strip()
        # Se prueban ambos: un documento con forma de correo no debe tapar la
        # cuenta interna que usa ese correo.
        candidatos = [
            *Usuario.objects.filter(nombre_usuario=identificador),
            *Usuario.objects.filter(email=identificador),
        ]
        if not candidatos:
            # Mismo costo que con un usuario existente: no revela si existe.
            Usuario().set_password(password)
            return None

        for usuario in candidatos:
            if usuario.check_password(password) and self.user_can_authenticate(usuario):
                return usuario
        return None
