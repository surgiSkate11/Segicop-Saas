from django import forms

from apps.antecedentes.models import EstadoVerificacion, ResultadoAntecedentes


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
        label="Vigente hasta (obligatorio si se valida)",
        required=False,
        widget=forms.DateInput(attrs={"class": "sc-input", "type": "date"}),
    )
    comentario = forms.CharField(required=False, widget=forms.Textarea(attrs={"class": "sc-input", "rows": 2}))
