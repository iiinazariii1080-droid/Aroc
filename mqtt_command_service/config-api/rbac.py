"""Role-based access control definitions."""

from enum import StrEnum


class Role(StrEnum):
    READ = "read"
    SERVICE = "service"
    WRITE = "write"
    ADMIN = "admin"


ROLE_HIERARCHY: dict[Role, int] = {
    Role.READ: 1,
    Role.SERVICE: 2,
    Role.WRITE: 3,
    Role.ADMIN: 4,
}
