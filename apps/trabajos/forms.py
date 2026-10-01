from django import forms

from apps.identidad.models import Instalacion
from apps.personas.models import Persona
from apps.trabajos.models import NivelRiesgo


class CrearPermisoTrabajoForm(forms.Form):
    instalacion = forms.ModelChoiceField(
        queryset=Instalacion.objects.none(), widget=forms.Select(attrs={"class": "sc-input"})
    )
    actividad = forms.CharField(widget=forms.Textarea(attrs={"class": "sc-input", "rows": 2}))
    nivel_riesgo = forms.ChoiceField(choices=NivelRiesgo.choices, widget=forms.Select(attrs={"class": "sc-input"}))
    integrantes = forms.ModelMultipleChoiceField(
        queryset=Persona.objects.none(), widget=forms.SelectMultiple(attrs={"class": "sc-input"})
    )
    responsable_cuadrilla = forms.ModelChoiceField(
        queryset=Persona.objects.none(), required=False, widget=forms.Select(attrs={"class": "sc-input"})
    )
    fecha_inicio_programada = forms.DateField(widget=forms.DateInput(attrs={"class": "sc-input", "type": "date"}))
    fecha_fin_programada = forms.DateField(widget=forms.DateInput(attrs={"class": "sc-input", "type": "date"}))

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        if empresa is not None:
            personas_ids = empresa.asignaciones.filter(activa=True).values_list("persona_id", flat=True)
            queryset_personas = Persona.objects.filter(id__in=personas_ids)
            self.fields["integrantes"].queryset = queryset_personas
            self.fields["responsable_cuadrilla"].queryset = queryset_personas
            self.fields["instalacion"].queryset = Instalacion.objects.filter(
                organizacion=empresa.organizacion, activa=True
            )
