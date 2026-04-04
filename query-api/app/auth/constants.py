from enum import Enum


class UserRole(str, Enum):
    DRIVER = "driver"
    DISPATCHER = "dispatcher"
    ADMIN = "admin"
