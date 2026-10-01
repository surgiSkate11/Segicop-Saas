from django import forms

from apps.core.forms import EstiloFormMixin
from apps.empresas.models import EmpresaContratista
from apps.personas.models import TipoDocumento


class PersonaForm(EstiloFormMixin, forms.Form):
    tipo_documento = forms.ChoiceField(label="Tipo de documento", choices=TipoDocumento.choices)
    numero_documento = forms.CharField(label="N° de documento", max_length=30)
    nombres = forms.CharField(max_length=150)
    apellidos = forms.CharField(max_length=150)
    fecha_nacimiento = forms.DateField(
        label="Fecha de nacimiento", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    contacto_telefono = forms.CharField(label="Teléfono", max_length=30, required=False)
    contacto_email = forms.EmailField(label="Correo", required=False)


class AsignarPersonaForm(EstiloFormMixin, forms.Form):
    empresa = forms.ModelChoiceField(queryset=EmpresaContratista.objects.none())
    cargo_o_actividad = forms.CharField(label="Cargo o actividad", max_length=150)
    fecha_inicio = forms.DateField(label="Desde", widget=forms.DateInput(attrs={"type": "date"}))
    fecha_fin = forms.DateField(
        label="Hasta (opcional)", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )

    def __init__(self, *args, organizacion=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organizacion is not None:
            self.fields["empresa"].queryset = EmpresaContratista.objects.filter(organizacion=organizacion).order_by(
                "razon_social"
            )
