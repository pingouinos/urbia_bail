"""Les contrats archivés se corrigent par le parcours versionné, jamais en admin brut."""
class ContratArchiveAdminMixin:
    def has_change_permission(self, request, obj=None):
        if obj and (obj.signe or obj.versions_document.exists()):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and (obj.signe or obj.versions_document.exists()):
            return False
        return super().has_delete_permission(request, obj)
