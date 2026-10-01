from django import forms

from apps.accesos.models import TipoEvento
from apps.identidad.models import Instalacion


class SolicitarPermisoForm(forms.Form):
    instalacion = forms.ModelChoiceField(
        queryset=Instalacion.objects.none(), widget=forms.Select(attrs={"class": "sc-input"})
    )
    fecha_inicio_vigencia = forms.DateField(widget=forms.DateInput(attrs={"class": "sc-input", "type": "date"}))
    fecha_fin_vigencia = forms.DateField(widget=forms.DateInput(attrs={"class": "sc-input", "type": "date"}))

    def __init__(self, *args, organizacion=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organizacion is not None:
            self.fields["instalacion"].queryset = Instalacion.objects.filter(organizacion=organizacion, activa=True)


class GaritaForm(forms.Form):
    codigo = forms.UUIDField(
        label="Código del permiso",
        widget=forms.TextInput(attrs={"class": "sc-input", "placeholder": "Pega o escanea el código"}),
    )
    instalacion = forms.ModelChoiceField(
        queryset=Instalacion.objects.none(), widget=forms.Select(attrs={"class": "sc-input"})
    )
    tipo = forms.ChoiceField(
        choices=[(TipoEvento.ENTRADA, "Registrar entrada"), (TipoEvento.SALIDA, "Registrar salida")],
        widget=forms.RadioSelect,
    )

    def __init__(self, *args, organizacion=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organizacion is not None:
            self.fields["instalacion"].queryset = Instalacion.objects.filter(organizacion=organizacion, activa=True)
