from django import forms


class EstiloFormMixin:
    """
    Agrega la clase `sc-input` a todos los widgets de un formulario, para
    no repetir `attrs={"class": "sc-input"}` en cada campo de cada form.
    Se omiten checkbox/radio, que no usan ese estilo.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            if isinstance(campo.widget, (forms.CheckboxInput, forms.RadioSelect, forms.CheckboxSelectMultiple)):
                continue
            existente = campo.widget.attrs.get("class", "")
            if "sc-input" not in existente:
                campo.widget.attrs["class"] = f"{existente} sc-input".strip()
