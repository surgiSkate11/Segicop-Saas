from datetime import date

from django import forms

from apps.antecedentes.models import EstadoVerificacion, MedioAutorizacion, ResultadoAntecedentes
from apps.core.forms import EstiloFormMixin


class RegistrarResultadoManualForm(forms.Form):
    resultado = forms.ChoiceField(choices=ResultadoAntecedentes.choices, widget=forms.RadioSelect)
    numero_certificado = forms.CharField(
        label="N° de certificado", required=False, widget=forms.TextInput(attrs={"class": "sc-input"})
    )
    proveedor = forms.CharField(
        label="Fuente consultada", required=False, widget=forms.TextInput(attrs={"class": "sc-input"})
    )
    comentario = forms.CharField(required=False, widget=forms.Textarea(attrs={"class": "sc-input", "rows": 2}))


class ValidarVerificacionForm(forms.Form):
    DECISIONES = [
        (EstadoVerificacion.VALIDADA, "Validar"),
        (EstadoVerificacion.LISTA, "Devolver para una nueva carga"),
        (EstadoVerificacion.CANCELADA, "Anular la solicitud"),
    ]
    decision = forms.ChoiceField(choices=DECISIONES, widget=forms.RadioSelect)
    vigente_hasta = forms.DateField(
        label="Vigente hasta (por defecto, la del certificado; solo puede acortarse)",
        required=False,
        widget=forms.DateInput(attrs={"class": "sc-input", "type": "date"}, format="%Y-%m-%d"),
    )
    comentario = forms.CharField(required=False, widget=forms.Textarea(attrs={"class": "sc-input", "rows": 2}))


class AutorizacionDatosForm(EstiloFormMixin, forms.Form):
    """La finalidad no se elige: es la de la verificación que espera la autorización."""

    medio = forms.ChoiceField(choices=MedioAutorizacion.choices, initial=MedioAutorizacion.DOCUMENTO_FIRMADO)
    referencia_evidencia = forms.CharField(
        label="Referencia de la evidencia (obligatoria si es documento firmado u otro)", required=False, max_length=255
    )
    otorgada_en = forms.DateField(
        label="Fecha en que se otorgó",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=date.today,
    )
    version = forms.CharField(label="Versión del texto", max_length=30, initial="1.0")
    texto = forms.CharField(label="Texto aceptado por la persona", widget=forms.Textarea(attrs={"rows": 4}))

    def __init__(self, *args, finalidad_label="", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["texto"].initial = (
            "Autorizo la consulta de mis antecedentes penales ante el Ministerio del Interior "
            f"con la finalidad de: {finalidad_label.lower()}."
        )


class RevocarAutorizacionForm(EstiloFormMixin, forms.Form):
    motivo = forms.CharField(
        label="Motivo de la revocación",
        help_text=(
            "Se anulan las verificaciones en curso y una ya validada deja de habilitar el ingreso; "
            "el historial se conserva."
        ),
        widget=forms.Textarea(attrs={"rows": 3}),
    )
