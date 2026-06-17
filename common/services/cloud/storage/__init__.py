"""Cloud storage abstractions and provider selection helpers."""

from common.services.cloud.storage.base import StorageService
from common.services.cloud.storage.factory import get_storage_service

__all__ = ["StorageService", "get_storage_service"]
