from django import forms

from apps.core.forms import EstiloFormMixin
from apps.cumplimiento.models import AplicaA, Revision
from apps.empresas.models import EmpresaContratista
from apps.personas.models import Persona


class SubirDocumentoForm(forms.Form):
    archivo = forms.FileField(label="Archivo", widget=forms.ClearableFileInput(attrs={"class": "sc-input"}))
    fecha_vencimiento = forms.DateField(
        label="Fecha de vencimiento (opcional)",
        required=False,
        widget=forms.DateInput(attrs={"class": "sc-input", "type": "date"}),
    )


class RevisionForm(forms.Form):
    decision = forms.ChoiceField(choices=Revision.Decision.choices, widget=forms.RadioSelect)
    comentario = forms.CharField(
        label="Comentario", required=False, widget=forms.Textarea(attrs={"class": "sc-input", "rows": 3})
    )


class CatalogoRequisitoForm(EstiloFormMixin, forms.Form):
    codigo = forms.SlugField(
        label="Código", max_length=60, help_text="Identificador corto, sin espacios (ej. ruc-vigente)."
    )
    nombre = forms.CharField(max_length=200)
    descripcion = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    aplica_a = forms.ChoiceField(choices=AplicaA.choices)
    obligatorio = forms.BooleanField(required=False, initial=True, label="Es obligatorio")
    vigencia_dias = forms.IntegerField(
        label="Vigencia en días (opcional)",
        required=False,
        min_value=1,
        help_text="Vacío = el documento no vence.",
    )


class AsignarRequisitoForm(EstiloFormMixin, forms.Form):
    empresa = forms.ModelChoiceField(queryset=EmpresaContratista.objects.none(), required=False)
    persona = forms.ModelChoiceField(queryset=Persona.objects.none(), required=False)
    fecha_limite = forms.DateField(
        label="Fecha límite (opcional)", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )

    def __init__(self, *args, organizacion=None, catalogo=None, **kwargs):
        super().__init__(*args, **kwargs)
        if catalogo and catalogo.aplica_a == AplicaA.EMPRESA:
            del self.fields["persona"]
            self.fields["empresa"].required = True
            if organizacion is not None:
                self.fields["empresa"].queryset = EmpresaContratista.objects.filter(organizacion=organizacion).order_by(
                    "razon_social"
                )
        elif catalogo:
            del self.fields["empresa"]
            self.fields["persona"].required = True
            if organizacion is not None:
                self.fields["persona"].queryset = Persona.objects.filter(organizacion=organizacion).order_by(
                    "apellidos"
                )
