from django.urls import path

from apps.balance import views

urlpatterns = [
    path("documents", views.document_collection, name="balance-document-collection"),
    path(
        "documents/<uuid:document_id>",
        views.document_detail,
        name="balance-document-detail",
    ),
    path(
        "documents/<uuid:document_id>/export.xlsx",
        views.export_document,
        name="balance-document-export",
    ),
    path(
        "documents/<uuid:document_id>/reprocess",
        views.reprocess_document,
        name="balance-document-reprocess",
    ),
]
