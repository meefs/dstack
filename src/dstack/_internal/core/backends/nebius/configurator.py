import json

from nebius.aio.service_error import RequestError

from dstack._internal.core.backends.base.configurator import (
    BackendRecord,
    Configurator,
    raise_invalid_credentials_error,
)
from dstack._internal.core.backends.nebius import resources
from dstack._internal.core.backends.nebius.backend import NebiusBackend
from dstack._internal.core.backends.nebius.models import (
    AnyNebiusBackendConfig,
    NebiusBackendConfig,
    NebiusBackendConfigWithCreds,
    NebiusConfig,
    NebiusCreds,
    NebiusServiceAccountCreds,
    NebiusStoredConfig,
)
from dstack._internal.core.models.backends.base import BackendType


class NebiusConfigurator(Configurator):
    TYPE = BackendType.NEBIUS
    BACKEND_CLASS = NebiusBackend

    def validate_config(self, config: NebiusBackendConfigWithCreds, default_creds_enabled: bool):
        assert isinstance(config.creds, NebiusServiceAccountCreds)
        try:
            sdk = resources.make_sdk(config.creds)
            available_regions = set(resources.get_region_to_project_id_map(sdk))
        except (ValueError, RequestError) as e:
            raise_invalid_credentials_error(
                fields=[["creds"]],
                details=str(e),
            )
        if invalid_regions := set(config.regions or []) - available_regions:
            raise_invalid_credentials_error(
                fields=[["regions"]],
                details=(
                    f"Configured regions {invalid_regions} do not exist in this Nebius tenancy."
                    " Omit `regions` to use all regions or select some of the available regions:"
                    f" {available_regions}"
                ),
            )

    def create_backend(
        self, project_name: str, config: NebiusBackendConfigWithCreds
    ) -> BackendRecord:
        return BackendRecord(
            config=NebiusStoredConfig(
                **NebiusBackendConfig.__response__.parse_obj(config).dict()
            ).json(),
            auth=NebiusCreds.parse_obj(config.creds).json(),
        )

    def get_backend_config(
        self, record: BackendRecord, include_creds: bool
    ) -> AnyNebiusBackendConfig:
        config = self._get_config(record)
        if include_creds:
            return NebiusBackendConfigWithCreds.__response__.parse_obj(config)
        return NebiusBackendConfig.__response__.parse_obj(config)

    def get_backend(self, record: BackendRecord) -> NebiusBackend:
        config = self._get_config(record)
        return NebiusBackend(config=config)

    def _get_config(self, record: BackendRecord) -> NebiusConfig:
        return NebiusConfig.__response__(
            **json.loads(record.config),
            creds=NebiusCreds.parse_raw(record.auth),
        )
