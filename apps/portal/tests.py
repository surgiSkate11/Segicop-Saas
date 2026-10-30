from datetime import date

from django.test import TestCase
from django.urls import reverse

from apps.empresas.services import crear_contrato, crear_empresa
from apps.identidad.models import Organizacion, Rol, RolAsignado, Usuario
from apps.personas.models import TipoDocumento
from apps.personas.services import asignar_persona_a_empresa, registrar_persona

CLAVE = "Portal-Segura-2026"
HTMX = {"HTTP_HX_REQUEST": "true"}
RUC = "1790012345001"
CEDULA = "1710034065"


def preparar_staff(organizacion, email="admin@cliente.com"):
    """Personal interno con rol en la organización."""
    staff = Usuario.objects.create_user(email=email, nombre_completo="Admin Cliente", password=CLAVE)
    rol, _ = Rol.objects.get_or_create(codigo="admin_cliente", defaults={"nombre": "Administrador del cliente"})
    RolAsignado.objects.create(usuario=staff, organizacion=organizacion, rol=rol)
    return staff


def con_clave(usuario):
    usuario.set_password(CLAVE)
    usuario.save()
    return usuario


class PortalBase(TestCase):
    def setUp(self):
        self.organizacion = Organizacion.objects.create(nombre="Minera del Sur")
        self.staff = preparar_staff(self.organizacion)

    def crear_empresa(self, razon_social, ruc, organizacion=None, email=None):
        with self.captureOnCommitCallbacks(execute=True):
            return crear_empresa(
                organizacion=organizacion or self.organizacion,
                razon_social=razon_social,
                ruc=ruc,
                actor=self.staff,
                representante_legal=f"Rep. de {razon_social}",
                contacto_email=email or f"{ruc}@empresa.com",
            )

    def crear_persona(self, nombres, documento, organizacion=None):
        with self.captureOnCommitCallbacks(execute=True):
            return registrar_persona(
                organizacion=organizacion or self.organizacion,
                tipo_documento=TipoDocumento.CEDULA,
                numero_documento=documento,
                nombres=nombres,
                apellidos="Quispe",
                actor=self.staff,
                contacto_email=f"{documento}@correo.com",
            )

    def entrar(self, titular):
        con_clave(titular.usuario)
        self.assertTrue(self.client.login(username=titular.nombre_usuario_cuenta, password=CLAVE))

    def asignar(self, persona, empresa, cargo, contrato=None):
        return asignar_persona_a_empresa(
            persona=persona,
            empresa=empresa,
            cargo_o_actividad=cargo,
            fecha_inicio=date(2026, 1, 5),
            actor=self.staff,
            contrato=contrato,
        )

    def contrato(self, empresa, numero):
        return crear_contrato(
            empresa=empresa,
            numero=numero,
            objeto=f"Obras civiles {numero}",
            fecha_inicio=date(2026, 1, 1),
            fecha_fin=date(2026, 12, 31),
            actor=self.staff,
        )


class PortalEmpresaTests(PortalBase):
    """S4: la empresa ve solo sus datos, su personal y sus contratos."""

    def setUp(self):
        super().setUp()
        self.empresa = self.crear_empresa("Constructora Andina", RUC)
        self.otra = self.crear_empresa("Rival Ingeniería", "1790099999001")
        self.contrato(self.empresa, "CT-2026-001")
        self.contrato(self.otra, "CT-RIVAL-9")
        self.ana = self.crear_persona("Ana", CEDULA)
        self.beto = self.crear_persona("Beto", "1710034099")
        self.asignar(self.ana, self.empresa, "Soldadora")
        self.asignar(self.beto, self.otra, "Electricista")

    def test_tras_login_la_raiz_lleva_al_portal_de_la_empresa(self):
        self.entrar(self.empresa)

        respuesta = self.client.get(reverse("identidad:panel"))
        self.assertRedirects(respuesta, reverse("portal:inicio"), fetch_redirect_response=False)
        respuesta = self.client.get(reverse("portal:inicio"))
        self.assertRedirects(respuesta, reverse("portal:empresa"))

    def test_ve_sus_datos_estado_personal_y_contratos(self):
        self.entrar(self.empresa)

        respuesta = self.client.get(reverse("portal:empresa"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Constructora Andina")
        self.assertContains(respuesta, RUC)
        self.assertContains(respuesta, "Minera del Sur")
        self.assertContains(respuesta, "Rep. de Constructora Andina")
        self.assertContains(respuesta, self.empresa.get_estado_display())
        self.assertContains(respuesta, "Ana Quispe")
        self.assertContains(respuesta, CEDULA)
        self.assertContains(respuesta, "Soldadora")
        self.assertContains(respuesta, "CT-2026-001")
        self.assertContains(respuesta, "Obras civiles CT-2026-001")

    def test_muestra_el_motivo_del_estado(self):
        self.empresa.motivo_estado = "Falta el seguro de responsabilidad civil."
        self.empresa.save()
        self.entrar(self.empresa)

        respuesta = self.client.get(reverse("portal:empresa"))

        self.assertContains(respuesta, "Falta el seguro de responsabilidad civil.")

    def test_no_ve_otras_empresas_ni_su_personal_de_la_misma_organizacion(self):
        self.entrar(self.empresa)

        respuesta = self.client.get(reverse("portal:empresa"))

        self.assertNotContains(respuesta, "Rival Ingeniería")
        self.assertNotContains(respuesta, "1790099999001")
        self.assertNotContains(respuesta, "Beto")
        self.assertNotContains(respuesta, "Electricista")
        self.assertNotContains(respuesta, "CT-RIVAL-9")

    def test_no_lista_asignaciones_inactivas_ni_contratos_inactivos(self):
        asignacion = self.asignar(self.beto, self.empresa, "Gruista")
        asignacion.activa = False
        asignacion.save()
        contrato = self.contrato(self.empresa, "CT-VIEJO-7")
        contrato.activo = False
        contrato.save()
        self.entrar(self.empresa)

        respuesta = self.client.get(reverse("portal:empresa"))

        self.assertNotContains(respuesta, "Gruista")
        self.assertNotContains(respuesta, "CT-VIEJO-7")

    def test_cuenta_en_dos_organizaciones_ve_ambas_empresas(self):
        otra_organizacion = Organizacion.objects.create(nombre="Petrolera Norte")
        en_otra = self.crear_empresa(
            "Constructora Andina Norte", RUC, organizacion=otra_organizacion, email="norte@andina.com"
        )
        self.assertEqual(en_otra.usuario, self.empresa.usuario)
        self.entrar(self.empresa)

        respuesta = self.client.get(reverse("portal:empresa"))

        self.assertContains(respuesta, "Constructora Andina")
        self.assertContains(respuesta, "Constructora Andina Norte")
        self.assertContains(respuesta, "Minera del Sur")
        self.assertContains(respuesta, "Petrolera Norte")

    def test_empresa_sin_personal_ni_contratos_muestra_estados_vacios(self):
        sola = self.crear_empresa("Sola SA", "1790055555001")
        self.entrar(sola)

        respuesta = self.client.get(reverse("portal:empresa"))

        self.assertContains(respuesta, "sc-vacio", count=2)

    def test_el_portal_no_muestra_la_navegacion_interna(self):
        self.entrar(self.empresa)

        respuesta = self.client.get(reverse("portal:empresa"))

        self.assertNotContains(respuesta, reverse("empresas:lista"))
        self.assertNotContains(respuesta, reverse("admin:index"))
        self.assertContains(respuesta, reverse("identidad:logout"))


class PortalPersonaTests(PortalBase):
    """S5: la persona ve su perfil, sus asignaciones y el espacio del pase."""

    def setUp(self):
        super().setUp()
        self.empresa = self.crear_empresa("Constructora Andina", RUC)
        self.ana = self.crear_persona("Ana", CEDULA)
        self.otra = self.crear_persona("Beto", "1710034099")
        self.asignar(self.ana, self.empresa, "Soldadora")
        self.asignar(self.otra, self.empresa, "Electricista")

    def test_tras_login_la_raiz_lleva_al_portal_de_la_persona(self):
        self.entrar(self.ana)

        respuesta = self.client.get(reverse("identidad:panel"))
        self.assertRedirects(respuesta, reverse("portal:inicio"), fetch_redirect_response=False)
        respuesta = self.client.get(reverse("portal:inicio"))
        self.assertRedirects(respuesta, reverse("portal:persona"))

    def test_ve_sus_datos_asignacion_y_la_tarjeta_del_pase(self):
        self.entrar(self.ana)

        respuesta = self.client.get(reverse("portal:persona"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Ana Quispe")
        self.assertContains(respuesta, CEDULA)
        self.assertContains(respuesta, f"{CEDULA}@correo.com")
        self.assertContains(respuesta, self.ana.get_estado_display())
        self.assertContains(respuesta, "Constructora Andina")
        self.assertContains(respuesta, "Soldadora")
        self.assertContains(respuesta, "Mi pase de ingreso")
        self.assertContains(respuesta, "Disponible cuando se apruebe tu ingreso")

    def test_no_ve_a_otra_persona_de_la_misma_organizacion(self):
        self.entrar(self.ana)

        respuesta = self.client.get(reverse("portal:persona"))

        self.assertNotContains(respuesta, "Beto")
        self.assertNotContains(respuesta, "1710034099")
        self.assertNotContains(respuesta, "Electricista")

    def test_persona_sin_asignaciones_muestra_estado_vacio(self):
        sin_asignar = self.crear_persona("Carla", "1710034123")
        self.entrar(sin_asignar)

        respuesta = self.client.get(reverse("portal:persona"))

        self.assertContains(respuesta, "sc-vacio")


class PortalAutorizacionTests(PortalBase):
    """Cada portal es solo para su tipo de cuenta; el personal interno no entra."""

    def setUp(self):
        super().setUp()
        self.empresa = self.crear_empresa("Constructora Andina", RUC)
        self.persona = self.crear_persona("Ana", CEDULA)

    def test_empresa_recibe_403_en_el_portal_de_persona(self):
        self.entrar(self.empresa)
        self.assertEqual(self.client.get(reverse("portal:persona")).status_code, 403)

    def test_persona_recibe_403_en_el_portal_de_empresa(self):
        self.entrar(self.persona)
        self.assertEqual(self.client.get(reverse("portal:empresa")).status_code, 403)

    def test_personal_interno_recibe_403_en_ambos_portales(self):
        self.client.login(username=self.staff.email, password=CLAVE)

        self.assertEqual(self.client.get(reverse("portal:empresa")).status_code, 403)
        self.assertEqual(self.client.get(reverse("portal:persona")).status_code, 403)

    def test_inicio_lleva_al_personal_interno_a_su_panel(self):
        self.client.login(username=self.staff.email, password=CLAVE)

        respuesta = self.client.get(reverse("portal:inicio"))

        self.assertRedirects(respuesta, reverse("identidad:panel"))

    def test_anonimo_es_enviado_al_login(self):
        for nombre in ("portal:empresa", "portal:persona", "portal:inicio"):
            with self.subTest(nombre):
                url = reverse(nombre)
                respuesta = self.client.get(url)
                self.assertRedirects(
                    respuesta, f"{reverse('identidad:login')}?next={url}", fetch_redirect_response=False
                )


class AccesoPortalMiddlewareTests(PortalBase):
    """S6: las cuentas de portal solo alcanzan el portal (denegación por defecto)."""

    def setUp(self):
        super().setUp()
        self.empresa = self.crear_empresa("Constructora Andina", RUC)
        self.persona = self.crear_persona("Ana", CEDULA)

    def test_cuentas_de_portal_reciben_403_fuera_del_portal(self):
        urls = [
            reverse("empresas:lista"),
            reverse("personas:lista"),
            reverse("cumplimiento:bandeja"),
            reverse("accesos:lista"),
            reverse("identidad:alertas"),
            "/admin/",
            "/api/v1/schema/",
            "/ruta/que/no/existe/",
        ]
        for titular in (self.empresa, self.persona):
            self.client.logout()
            self.entrar(titular)
            for url in urls:
                with self.subTest(cuenta=titular.usuario.tipo_cuenta, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)

    def test_peticion_htmx_denegada_tambien_recibe_403(self):
        self.entrar(self.empresa)

        respuesta = self.client.get(reverse("empresas:crear"), **HTMX)

        self.assertEqual(respuesta.status_code, 403)

    def test_post_fuera_del_portal_recibe_403_y_no_crea_nada(self):
        self.entrar(self.empresa)
        antes = type(self.empresa).objects.count()

        respuesta = self.client.post(
            reverse("empresas:crear"),
            {"razon_social": "Intrusa", "ruc": "1790077777001", "contacto_email": "x@y.com"},
            **HTMX,
        )

        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(type(self.empresa).objects.count(), antes)

    def test_el_cierre_de_sesion_sigue_funcionando(self):
        self.entrar(self.empresa)

        respuesta = self.client.post(reverse("identidad:logout"))

        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(self.client.get(reverse("portal:empresa")).status_code, 302)

    def test_personal_interno_conserva_su_acceso(self):
        self.client.login(username=self.staff.email, password=CLAVE)

        self.assertEqual(self.client.get(reverse("empresas:lista")).status_code, 200)
        self.assertEqual(self.client.get(reverse("identidad:panel")).status_code, 200)

    def test_anonimo_conserva_el_flujo_de_login(self):
        self.assertEqual(self.client.get(reverse("identidad:login")).status_code, 200)
        self.assertEqual(self.client.get(reverse("empresas:lista")).status_code, 302)
