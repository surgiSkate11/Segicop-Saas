from django import forms

from apps.cumplimiento.models import Revision


class SubirDocumentoForm(forms.Form):
    archivo = forms.FileField(label="Archivo", widget=forms.ClearableFileInput(attrs={"class": "sc-input"}))
    fecha_vencimiento = forms.DateField(
        label="Fecha de vencimiento (opcional)", required=False,
        widget=forms.DateInput(attrs={"class": "sc-input", "type": "date"}),
    )


class RevisionForm(forms.Form):
    decision = forms.ChoiceField(choices=Revision.Decision.choices, widget=forms.RadioSelect)
    comentario = forms.CharField(
        label="Comentario", required=False, widget=forms.Textarea(attrs={"class": "sc-input", "rows": 3})
    )