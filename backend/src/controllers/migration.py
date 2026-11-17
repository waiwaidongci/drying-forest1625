from ..services import migration
class MigrationController:
    status=staticmethod(migration.status)
    sweep=staticmethod(migration.sweep)
    verify=staticmethod(migration.verify)
    activate=staticmethod(migration.activate)
    resolve=staticmethod(migration.resolve_quarantine)
    equivalence=staticmethod(migration.verify_equivalence)
