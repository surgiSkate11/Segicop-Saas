from django import forms

from apps.core.forms import EstiloFormMixin
from apps.identidad.forms import CorreoAccesoFormMixin


class EmpresaForm(CorreoAccesoFormMixin, EstiloFormMixin, forms.Form):
    razon_social = forms.CharField(label="Razón social", max_length=255)
    ruc = forms.CharField(label="RUC", max_length=20)
    representante_legal = forms.CharField(label="Representante legal", max_length=255, required=False)
    contacto_email = forms.EmailField(label="Correo de contacto", required=False)
    contacto_telefono = forms.CharField(label="Teléfono de contacto", max_length=30, required=False)
