"""Remote registry client for fetching and validating add-on metadata over HTTPS."""

import os
from typing import Any

import httpx

from aiaddons.core.exceptions import AIAddonsError, ManifestValidationError, RegistryFetchError
from aiaddons.registry.models import RegistryIndex
from aiaddons.registry.validator import validate_registry_data

DEFAULT_REGISTRY_URL = "https://registry.aiaddons.dev/index.json"
ALLOWED_HTTP_HOSTS = {"localhost", "127.0.0.1"}


class RegistryClient:
    """Client responsible solely for retrieving and validating remote registry metadata."""

    def __init__(
        self,
        registry_url: str | None = None,
        timeout: float = 10.0,
        httpx_client: httpx.Client | None = None,
    ) -> None:
        if registry_url is None:
            registry_url = os.environ.get("AIADDONS_REGISTRY_URL", DEFAULT_REGISTRY_URL)
        self.registry_url = registry_url.strip()
        self.timeout = timeout
        self._custom_client = httpx_client

    def _validate_endpoint_url(self, url: str) -> None:
        """Enforce HTTPS for remote endpoints, allowing http only for local dev/testing."""
        lowered = url.lower()
        if lowered.startswith("http://"):
            stripped = lowered[7:]
            host = stripped.split("/")[0].split(":")[0]
            if host not in ALLOWED_HTTP_HOSTS:
                raise RegistryFetchError(
                    f"Security violation: Insecure HTTP endpoint '{url}' is prohibited. "
                    "Registry endpoints must use HTTPS."
                )
        elif not lowered.startswith("https://"):
            raise RegistryFetchError(
                f"Invalid registry URL scheme in '{url}'. Only HTTPS endpoints are supported."
            )

    def fetch_registry(self) -> RegistryIndex:
        """Fetch and validate registry metadata from the configured trusted endpoint."""
        self._validate_endpoint_url(self.registry_url)

        try:
            if self._custom_client is not None:
                response = self._custom_client.get(self.registry_url, timeout=self.timeout)
            else:
                with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                    response = client.get(self.registry_url)

            if response.status_code != 200:
                raise RegistryFetchError(
                    f"Failed to fetch registry from '{self.registry_url}': "
                    f"HTTP status {response.status_code}."
                )

            try:
                raw_data: Any = response.json()
            except Exception as json_err:
                raise RegistryFetchError(
                    f"Failed to parse JSON response from registry '{self.registry_url}': {json_err}"
                ) from json_err

        except httpx.TimeoutException as err:
            raise RegistryFetchError(
                f"Connection timeout while fetching registry from '{self.registry_url}'."
            ) from err
        except httpx.HTTPError as err:
            raise RegistryFetchError(
                f"HTTP request error while fetching registry from '{self.registry_url}': {err}"
            ) from err

        try:
            return validate_registry_data(raw_data, source_label=self.registry_url)
        except (ManifestValidationError, AIAddonsError) as err:
            raise RegistryFetchError(
                f"Registry validation failed for downloaded index from '{self.registry_url}': {err}"
            ) from err
