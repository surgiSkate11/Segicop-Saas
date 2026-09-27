import uuid
from django.db import models

class BaseModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]

class OrganizacionScopedModel(BaseModel):
    organizacion = models.ForeignKey(
        "identidad.Organizacion", on_delete=models.PROTECT, related_name="%(class)ss"
    )
    class Meta(BaseModel.Meta):
        abstract = True