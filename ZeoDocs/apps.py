from django.apps import AppConfig


class ZeoDocsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ZeoDocs'
    verbose_name = 'Document Center (DMS): folders, documents, versions, approvals, distribution'

    def ready(self):
        from . import hooks
        hooks.connect()
        # v1.70.7: file fields of documents and folders are kept as their attachments – who may open them follows the Document Center rights
        try:
            from Chatter.views import register_scope
            from . import models as Mo

            def docs(request, qs):
                from . import services as S
                who = S.Who(request)
                return qs.filter(pk__in=[d.pk for d in qs if 'view' in S.doc_rights(d, who)])

            def folders(request, qs):
                from . import services as S
                who = S.Who(request)
                return qs.filter(pk__in=[f.pk for f in qs if 'view' in S.folder_rights(f, who)])
            register_scope(Mo.Document, docs)
            register_scope(Mo.DocFolder, folders)
        except Exception:   # noqa: BLE001
            pass
