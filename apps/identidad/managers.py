from django.contrib.auth.base_user import BaseUserManager


class UsuarioManager(BaseUserManager):
    """
    RF-004 pide poder invitar y recuperar cuentas "sin revelar si una
    persona ya existe": por eso el username field es el email, y
    create_user nunca debe usarse para dar pistas de existencia hacia
    el usuario final (eso se resuelve en la vista/servicio de invitación).
    """

    use_in_migrations = True

    def _crear_usuario(self, email, password, **extra_fields):
        if not email:
            raise ValueError("El usuario debe tener un correo electrónico.")
        email = self.normalize_email(email)
        usuario = self.model(email=email, **extra_fields)
        usuario.set_password(password)
        usuario.save(using=self._db)
        return usuario

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._crear_usuario(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("El superusuario requiere is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("El superusuario requiere is_superuser=True.")

        return self._crear_usuario(email, password, **extra_fields)