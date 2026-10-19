from django.contrib.auth.tokens import PasswordResetTokenGenerator


class ActivacionCuentaTokenGenerator(PasswordResetTokenGenerator):
    """
    Token del enlace de activación (S2). Hereda de Django:
    - un solo uso: el hash incluye la contraseña, así que deja de servir
      en cuanto el titular define la suya;
    - vencimiento: settings.PASSWORD_RESET_TIMEOUT (72 h).
    Un `key_salt` propio impide usar un token de restablecimiento aquí.
    """

    key_salt = "apps.identidad.tokens.ActivacionCuentaTokenGenerator"


activacion_cuenta_token = ActivacionCuentaTokenGenerator()
