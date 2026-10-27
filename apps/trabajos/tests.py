from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse

from apps.antecedentes.models import EstadoVerificacion, ModoConector, ResultadoAntecedentes, VerificacionAntecedentes
from apps.core.choices import EstadoCumplimiento
from apps.core.models import EventoAuditoria
from apps.empresas.models import EmpresaContratista
from apps.identidad.models import Instalacion, Organizacion, Rol, RolAsignado, Usuario
from apps.personas.models import Asignacion, Persona, TipoDocumento
from apps.trabajos.models import EstadoTrabajo, NivelRiesgo, PermisoTrabajo
from apps.trabajos.services import abrir_permiso_trabajo, crear_permiso_trabajo

HTMX = {"HTTP_HX_REQUEST": "true"}


def usuario_con_rol(organizacion, codigo, activo=True):
    usuario = Usuario.objects.create_user(
        email=f"{codigo}@cliente.com", nombre_completo=codigo, password="UnaClaveSegura123!"
    )
    rol, _ = Rol.objects.get_or_create(codigo=codigo, defaults={"nombre": codigo})
    RolAsignado.objects.create(usuario=usuario, organizacion=organizacion, rol=rol, activo=activo)
    return usuario


class TrabajosBase(TestCase):
    def setUp(self):
        self.organizacion = Organizacion.objects.create(nombre="Organización de trabajos")
        self.usuario = Usuario.objects.create_user(
            email="supervisor@example.com", nombre_completo="Supervisor de trabajos"
        )
        self.instalacion = Instalacion.objects.create(organizacion=self.organizacion, nombre="Instalación de trabajo")
        self.empresa = EmpresaContratista.objects.create(
            organizacion=self.organizacion,
            razon_social="Empresa de trabajo",
            ruc="1790000000004",
            estado=EstadoCumplimiento.HABILITADO,
        )
        self.persona = Persona.objects.create(
            organizacion=self.organizacion,
            tipo_documento=TipoDocumento.CEDULA,
            numero_documento="1212121212",
            nombres="Trabajador",
            apellidos="Habilitado",
            estado=EstadoCumplimiento.HABILITADO,
        )
        Asignacion.objects.create(
            organizacion=self.organizacion,
            persona=self.persona,
            empresa=self.empresa,
            cargo_o_actividad="Técnico",
            fecha_inicio=date.today(),
        )

    def crear_antecedentes(self, resultado=ResultadoAntecedentes.NO):
        return VerificacionAntecedentes.objects.create(
            organizacion=self.organizacion,
            persona=self.persona,
            estado=EstadoVerificacion.VALIDADA,
            modo_conector=ModoConector.MANUAL,
            resultado=resultado,
            vigente_hasta=date.today() + timedelta(days=30),
            validado_por=self.usuario,
        )

    def crear_permiso(self):
        return crear_permiso_trabajo(
            empresa=self.empresa,
            instalacion=self.instalacion,
            actividad="Mantenimiento preventivo",
            nivel_riesgo=NivelRiesgo.MEDIO,
            integrantes=[self.persona],
            actor=self.usuario,
            fecha_inicio_programada=date.today(),
            fecha_fin_programada=date.today() + timedelta(days=1),
        )


class TrabajosTests(TrabajosBase):
    def test_rechaza_permiso_si_la_cuadrilla_no_esta_habilitada(self):
        permiso = self.crear_permiso()

        self.assertEqual(permiso.estado, EstadoTrabajo.RECHAZADO)
        self.assertIn("antecedentes", permiso.motivo)

    def test_abre_permiso_si_la_cuadrilla_esta_habilitada(self):
        self.crear_antecedentes()
        permiso = self.crear_permiso()

        abrir_permiso_trabajo(permiso=permiso, actor=self.usuario)

        permiso.refresh_from_db()
        self.assertEqual(permiso.estado, EstadoTrabajo.ACTIVO)
        self.assertEqual(permiso.aprobado_por_id, self.usuario.id)


class TrabajosPermisosTests(TrabajosBase):
    """S3: las vistas de trabajos deniegan por defecto según la matriz de roles."""

    def setUp(self):
        super().setUp()
        self.datos = {
            "instalacion": self.instalacion.pk,
            "actividad": "Mantenimiento preventivo",
            "nivel_riesgo": NivelRiesgo.MEDIO,
            "integrantes": [self.persona.pk],
            "responsable_cuadrilla": self.persona.pk,
            "fecha_inicio_programada": date.today().isoformat(),
            "fecha_fin_programada": (date.today() + timedelta(days=1)).isoformat(),
        }

    def _entrar(self, codigo, activo=True):
        usuario = usuario_con_rol(self.organizacion, codigo, activo=activo)
        self.client.force_login(usuario)
        return usuario

    def _conteos(self):
        return (PermisoTrabajo.objects.count(), EventoAuditoria.objects.count())

    def test_garita_no_ve_ni_crea_trabajos(self):
        self._entrar("seguridad_garita")
        antes = self._conteos()

        self.assertEqual(self.client.get(reverse("trabajos:lista")).status_code, 403)
        self.assertEqual(self.client.get(reverse("trabajos:crear", args=[self.empresa.pk]), **HTMX).status_code, 403)
        respuesta = self.client.post(reverse("trabajos:crear", args=[self.empresa.pk]), self.datos, **HTMX)

        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(self._conteos(), antes)

    def test_gestor_crea_pero_no_gestiona_el_ciclo_del_trabajo(self):
        self.crear_antecedentes()
        self._entrar("gestor_contratista")

        respuesta = self.client.post(reverse("trabajos:crear", args=[self.empresa.pk]), self.datos, **HTMX)
        self.assertTrue(respuesta.headers["HX-Redirect"].startswith("/trabajos/"))
        permiso = PermisoTrabajo.objects.get()
        estado_inicial = permiso.estado
        antes = self._conteos()

        self.assertEqual(self.client.get(reverse("trabajos:detalle", args=[permiso.pk])).status_code, 200)
        denegada = self.client.post(reverse("trabajos:detalle", args=[permiso.pk]), {"accion": "abrir"})

        self.assertEqual(denegada.status_code, 403)
        permiso.refresh_from_db()
        self.assertEqual(permiso.estado, estado_inicial)
        self.assertNotEqual(permiso.estado, EstadoTrabajo.ACTIVO)
        self.assertEqual(self._conteos(), antes)

    def test_responsable_de_area_crea_y_gestiona(self):
        self.crear_antecedentes()
        self._entrar("responsable_area")

        self.client.post(reverse("trabajos:crear", args=[self.empresa.pk]), self.datos, **HTMX)
        permiso = PermisoTrabajo.objects.get()
        self.client.post(reverse("trabajos:detalle", args=[permiso.pk]), {"accion": "abrir"})

        permiso.refresh_from_db()
        self.assertEqual(permiso.estado, EstadoTrabajo.ACTIVO)

    def test_revisor_documental_ve_pero_no_crea_ni_gestiona(self):
        self.crear_antecedentes()
        permiso = self.crear_permiso()
        self._entrar("revisor_documental")
        antes = self._conteos()

        self.assertEqual(self.client.get(reverse("trabajos:lista")).status_code, 200)
        self.assertEqual(self.client.get(reverse("trabajos:detalle", args=[permiso.pk])).status_code, 200)
        crear = self.client.post(reverse("trabajos:crear", args=[self.empresa.pk]), self.datos, **HTMX)
        gestionar = self.client.post(reverse("trabajos:detalle", args=[permiso.pk]), {"accion": "abrir"})

        self.assertEqual((crear.status_code, gestionar.status_code), (403, 403))
        self.assertEqual(self._conteos(), antes)

    def test_rol_inactivo_no_otorga_nada(self):
        self._entrar("responsable_area", activo=False)

        self.assertEqual(self.client.get(reverse("trabajos:lista")).status_code, 403)

    def test_admin_cliente_conserva_todo_el_acceso(self):
        self.crear_antecedentes()
        self._entrar("admin_cliente")

        self.assertEqual(self.client.get(reverse("trabajos:lista")).status_code, 200)
        self.client.post(reverse("trabajos:crear", args=[self.empresa.pk]), self.datos, **HTMX)
        permiso = PermisoTrabajo.objects.get()
        self.client.post(reverse("trabajos:detalle", args=[permiso.pk]), {"accion": "abrir"})
        permiso.refresh_from_db()
        self.assertEqual(permiso.estado, EstadoTrabajo.ACTIVO)
