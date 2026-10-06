"""Role -> permission map. This is the ONE place that defines who can do what.

Services call ``session.require(...)`` so rules hold even if a UI button is
reachable; the UI uses ``session.can(...)`` to disable buttons up front.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import config
from .errors import PermissionDenied

ALL_PERMISSIONS = frozenset({
    "sales.create",        
    "returns.process",     
    "inventory.view",      
    "inventory.manage",    
    "inventory.import",    
    "inventory.export",    
    "stock.view",          
    "stock.adjust",        
    "purchasing.manage",   
    "analytics.view",
    "audit.view",
    "customers.view",
    "system.backup",
    "account.update",      
    "barcode.generate",
})

PERMISSIONS_BY_ROLE: dict[str, frozenset[str]] = {
    config.ROLE_ADMIN: ALL_PERMISSIONS,
    config.ROLE_CASHIER: frozenset({"sales.create", "inventory.view", "barcode.generate", "account.update"}),
}


@dataclass(frozen=True)
class Session:
    user_id: int
    username: str
    role: str
    must_change_password: bool = False

    def can(self, permission: str) -> bool:
        return permission in PERMISSIONS_BY_ROLE.get(self.role, frozenset())

    def require(self, permission: str) -> None:
        if not self.can(permission):
            raise PermissionDenied(f"Your role ({self.role}) is not allowed to perform this action.")