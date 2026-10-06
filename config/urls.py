from django.urls import include, path

from apps.core import views as probes

urlpatterns = [
    # Psychotropic ledgers (ร.ว.จ ๗/๔, บ.ว.จ ๗/๔-ขพ, ร.ค.-๔).
    path("api/v1/balance/", include("apps.balance.urls")),
    path("health", probes.health, name="health"),
    path("ready", probes.ready, name="ready"),
]
