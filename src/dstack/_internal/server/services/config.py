from pathlib import Path
from typing import Dict, List, Literal, Optional, Union

import yaml
from pydantic import BaseModel, Field, ValidationError, root_validator
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import Annotated

from dstack._internal.core.errors import (
    BackendNotAvailable,
    ResourceNotExistsError,
    ServerClientError,
)
from dstack._internal.core.models.backends import AnyConfigInfoWithCreds, BackendInfoYAML
from dstack._internal.core.models.backends.aws import AnyAWSCreds, AWSOSImageConfig
from dstack._internal.core.models.backends.azure import AnyAzureCreds
from dstack._internal.core.models.backends.base import BackendType
from dstack._internal.core.models.backends.cudo import AnyCudoCreds
from dstack._internal.core.models.backends.datacrunch import AnyDataCrunchCreds
from dstack._internal.core.models.backends.kubernetes import KubernetesNetworkingConfig
from dstack._internal.core.models.backends.lambdalabs import AnyLambdaCreds
from dstack._internal.core.models.backends.oci import AnyOCICreds
from dstack._internal.core.models.backends.runpod import AnyRunpodCreds
from dstack._internal.core.models.backends.tensordock import AnyTensorDockCreds
from dstack._internal.core.models.backends.vastai import AnyVastAICreds
from dstack._internal.core.models.backends.vultr import AnyVultrCreds
from dstack._internal.core.models.common import CoreModel
from dstack._internal.server import settings
from dstack._internal.server.models import ProjectModel, UserModel
from dstack._internal.server.services import backends as backends_services
from dstack._internal.server.services import encryption as encryption_services
from dstack._internal.server.services import projects as projects_services
from dstack._internal.server.services.backends.handlers import delete_backends_safe
from dstack._internal.server.services.encryption import AnyEncryptionKeyConfig
from dstack._internal.server.services.permissions import (
    DefaultPermissions,
    set_default_permissions,
)
from dstack._internal.utils.common import run_async
from dstack._internal.utils.logging import get_logger

logger = get_logger(__name__)


# By default, PyYAML chooses the style of a collection depending on whether it has nested collections.
# If a collection has nested collections, it will be assigned the block style. Otherwise it will have the flow style.
#
# We want mapping to always be displayed in block-style but lists without nested objects in flow-style.
# So we define a custom representeter


def seq_representer(dumper, sequence):
    flow_style = len(sequence) == 0 or isinstance(sequence[0], str) or isinstance(sequence[0], int)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", sequence, flow_style)


yaml.add_representer(list, seq_representer)


# Below we define pydantic models for configs allowed in server/config.yml and YAML-based API.
# There are some differences between the two, e.g. server/config.yml fills file-based
# credentials by looking for a file, while YAML-based API doesn't do this.
# So for some backends there are two sets of config models.


class AWSConfig(CoreModel):
    type: Annotated[Literal["aws"], Field(description="The type of the backend")] = "aws"
    regions: Annotated[
        Optional[List[str]], Field(description="The list of AWS regions. Omit to use all regions")
    ] = None
    vpc_name: Annotated[
        Optional[str],
        Field(
            description=(
                "The name of custom VPCs. All configured regions must have a VPC with this name."
                " If your custom VPCs don't have names or have different names in different regions, use `vpc_ids` instead."
            )
        ),
    ] = None
    vpc_ids: Annotated[
        Optional[Dict[str, str]],
        Field(
            description=(
                "The mapping from AWS regions to VPC IDs."
                " If `default_vpcs: true`, omitted regions will use default VPCs"
            )
        ),
    ] = None
    default_vpcs: Annotated[
        Optional[bool],
        Field(
            description=(
                "A flag to enable/disable using default VPCs in regions not configured by `vpc_ids`."
                " Set to `false` if default VPCs should never be used."
                " Defaults to `true`"
            )
        ),
    ] = None
    public_ips: Annotated[
        Optional[bool],
        Field(
            description=(
                "A flag to enable/disable public IP assigning on instances."
                " `public_ips: false` requires at least one private subnet with outbound internet connectivity"
                " provided by a NAT Gateway or a Transit Gateway."
                " Defaults to `true`"
            )
        ),
    ] = None
    iam_instance_profile: Annotated[
        Optional[str],
        Field(
            description=(
                "The name of the IAM instance profile to associate with EC2 instances."
                " You can also specify the IAM role name for roles created via the AWS console."
                " AWS automatically creates an instance profile and gives it the same name as the role"
            )
        ),
    ] = None
    tags: Annotated[
        Optional[Dict[str, str]],
        Field(description="The tags that will be assigned to resources created by `dstack`"),
    ] = None
    os_images: Annotated[
        Optional[AWSOSImageConfig],
        Field(
            description="The mapping of instance categories (CPU, NVIDIA GPU) to AMI configurations"
        ),
    ] = None
    creds: AnyAWSCreds = Field(..., description="The credentials", discriminator="type")


class AzureConfig(CoreModel):
    type: Annotated[Literal["azure"], Field(description="The type of the backend")] = "azure"
    tenant_id: Annotated[str, Field(description="The tenant ID")]
    subscription_id: Annotated[str, Field(description="The subscription ID")]
    resource_group: Annotated[
        Optional[str],
        Field(
            description=(
                "The resource group for resources created by `dstack`."
                " If not specified, `dstack` will create a new resource group"
            )
        ),
    ] = None
    regions: Annotated[
        Optional[List[str]],
        Field(description="The list of Azure regions (locations). Omit to use all regions"),
    ] = None
    vpc_ids: Annotated[
        Optional[Dict[str, str]],
        Field(
            description=(
                "The mapping from configured Azure locations to network IDs."
                " A network ID must have a format `networkResourceGroup/networkName`"
                " If not specified, `dstack` will create a new network for every configured region"
            )
        ),
    ] = None
    public_ips: Annotated[
        Optional[bool],
        Field(
            description=(
                "A flag to enable/disable public IP assigning on instances."
                " `public_ips: false` requires `vpc_ids` that specifies custom networks with outbound internet connectivity"
                " provided by NAT Gateway or other mechanism."
                " Defaults to `true`"
            )
        ),
    ] = None
    tags: Annotated[
        Optional[Dict[str, str]],
        Field(description="The tags that will be assigned to resources created by `dstack`"),
    ] = None
    creds: AnyAzureCreds = Field(..., description="The credentials", discriminator="type")


class CudoConfig(CoreModel):
    type: Annotated[Literal["cudo"], Field(description="The type of backend")] = "cudo"
    regions: Annotated[
        Optional[List[str]], Field(description="The list of Cudo regions. Omit to use all regions")
    ] = None
    project_id: Annotated[str, Field(description="The project ID")]
    creds: Annotated[AnyCudoCreds, Field(description="The credentials")]


class DataCrunchConfig(CoreModel):
    type: Annotated[Literal["datacrunch"], Field(description="The type of backend")] = "datacrunch"
    regions: Annotated[
        Optional[List[str]],
        Field(description="The list of DataCrunch regions. Omit to use all regions"),
    ] = None
    creds: Annotated[AnyDataCrunchCreds, Field(description="The credentials")]


class GCPServiceAccountCreds(CoreModel):
    type: Annotated[Literal["service_account"], Field(description="The type of credentials")] = (
        "service_account"
    )
    filename: Annotated[str, Field(description="The path to the service account file")]
    data: Annotated[
        Optional[str],
        Field(
            description=(
                "The contents of the service account file."
                " When configuring via `server/config.yml`, it's automatically filled from `filename`."
                " When configuring via UI, it has to be specified explicitly"
            )
        ),
    ] = None

    @root_validator
    def fill_data(cls, values):
        return _fill_data(values)


class GCPServiceAccountAPICreds(CoreModel):
    type: Annotated[Literal["service_account"], Field(description="The type of credentials")] = (
        "service_account"
    )
    filename: Annotated[
        Optional[str], Field(description="The path to the service account file")
    ] = ""
    data: Annotated[str, Field(description="The contents of the service account file")]


class GCPDefaultCreds(CoreModel):
    type: Annotated[Literal["default"], Field(description="The type of credentials")] = "default"


AnyGCPCreds = Union[GCPServiceAccountCreds, GCPDefaultCreds]
AnyGCPAPICreds = Union[GCPServiceAccountAPICreds, GCPDefaultCreds]


class GCPConfig(CoreModel):
    type: Annotated[Literal["gcp"], Field(description="The type of backend")] = "gcp"
    project_id: Annotated[str, Field(description="The project ID")]
    regions: Annotated[
        Optional[List[str]], Field(description="The list of GCP regions. Omit to use all regions")
    ] = None
    vpc_name: Annotated[Optional[str], Field(description="The name of a custom VPC")] = None
    vpc_project_id: Annotated[
        Optional[str],
        Field(description="The shared VPC hosted project ID. Required for shared VPC only"),
    ] = None
    public_ips: Annotated[
        Optional[bool],
        Field(
            description="A flag to enable/disable public IP assigning on instances. Defaults to `true`"
        ),
    ] = None
    nat_check: Annotated[
        Optional[bool],
        Field(
            description=(
                "A flag to enable/disable a check that Cloud NAT is configured for the VPC."
                " This should be set to `false` when `public_ips: false` and outbound internet connectivity"
                " is provided by a mechanism other than Cloud NAT such as a third-party NAT appliance."
                " Defaults to `true`"
            )
        ),
    ] = None
    vm_service_account: Annotated[
        Optional[str], Field(description="The service account to associate with provisioned VMs")
    ] = None
    tags: Annotated[
        Optional[Dict[str, str]],
        Field(
            description="The tags (labels) that will be assigned to resources created by `dstack`"
        ),
    ] = None
    creds: AnyGCPCreds = Field(..., description="The credentials", discriminator="type")


class GCPAPIConfig(CoreModel):
    type: Annotated[Literal["gcp"], Field(description="The type of backend")] = "gcp"
    project_id: Annotated[str, Field(description="The project ID")]
    regions: Annotated[
        Optional[List[str]], Field(description="The list of GCP regions. Omit to use all regions")
    ] = None
    vpc_name: Annotated[Optional[str], Field(description="The name of a custom VPC")] = None
    vpc_project_id: Annotated[
        Optional[str],
        Field(description="The shared VPC hosted project ID. Required for shared VPC only"),
    ] = None
    public_ips: Annotated[
        Optional[bool],
        Field(
            description="A flag to enable/disable public IP assigning on instances. Defaults to `true`"
        ),
    ] = None
    nat_check: Annotated[
        Optional[bool],
        Field(
            description=(
                "A flag to enable/disable a check that Cloud NAT is configured for the VPC."
                " This should be set to `false` when `public_ips: false` and outbound internet connectivity"
                " is provided by a mechanism other than Cloud NAT such as a third-party NAT appliance."
                " Defaults to `true`"
            )
        ),
    ] = None
    vm_service_account: Annotated[
        Optional[str], Field(description="The service account associated with provisioned VMs")
    ] = None
    tags: Annotated[
        Optional[Dict[str, str]],
        Field(
            description="The tags (labels) that will be assigned to resources created by `dstack`"
        ),
    ] = None
    creds: AnyGCPAPICreds = Field(..., description="The credentials", discriminator="type")


class KubeconfigConfig(CoreModel):
    filename: Annotated[str, Field(description="The path to the kubeconfig file")]
    data: Annotated[
        Optional[str],
        Field(
            description=(
                "The contents of the kubeconfig file."
                " When configuring via `server/config.yml`, it's automatically filled from `filename`."
                " When configuring via UI, it has to be specified explicitly"
            )
        ),
    ] = None

    @root_validator
    def fill_data(cls, values):
        return _fill_data(values)


class KubeconfigAPIConfig(CoreModel):
    filename: Annotated[str, Field(description="The path to the kubeconfig file")] = ""
    data: Annotated[str, Field(description="The contents of the kubeconfig file")]


class KubernetesConfig(CoreModel):
    type: Annotated[Literal["kubernetes"], Field(description="The type of backend")] = "kubernetes"
    kubeconfig: Annotated[KubeconfigConfig, Field(description="The kubeconfig configuration")]
    networking: Annotated[
        Optional[KubernetesNetworkingConfig], Field(description="The networking configuration")
    ] = None


class KubernetesAPIConfig(CoreModel):
    type: Annotated[Literal["kubernetes"], Field(description="The type of backend")] = "kubernetes"
    kubeconfig: Annotated[KubeconfigAPIConfig, Field(description="The kubeconfig configuration")]
    networking: Annotated[
        Optional[KubernetesNetworkingConfig], Field(description="The networking configuration")
    ] = None


class LambdaConfig(CoreModel):
    type: Annotated[Literal["lambda"], Field(description="The type of backend")] = "lambda"
    regions: Annotated[
        Optional[List[str]],
        Field(description="The list of Lambda regions. Omit to use all regions"),
    ] = None
    creds: Annotated[AnyLambdaCreds, Field(description="The credentials")]


class NebiusServiceAccountCreds(CoreModel):
    type: Annotated[Literal["service_account"], Field(description="The type of credentials")] = (
        "service_account"
    )
    filename: Annotated[str, Field(description="The path to the service account file")]
    data: Annotated[
        Optional[str], Field(description="The contents of the service account file")
    ] = None

    @root_validator
    def fill_data(cls, values):
        return _fill_data(values)


class NebiusServiceAccountAPICreds(CoreModel):
    type: Annotated[Literal["service_account"], Field(description="The type of credentials")] = (
        "service_account"
    )
    filename: Annotated[str, Field(description="The path to the service account file")]
    data: Annotated[str, Field(description="The contents of the service account file")]


AnyNebiusCreds = NebiusServiceAccountCreds
AnyNebiusAPICreds = NebiusServiceAccountAPICreds


class NebiusConfig(CoreModel):
    type: Literal["nebius"] = "nebius"
    cloud_id: str
    folder_id: str
    network_id: str
    regions: Optional[List[str]] = None
    creds: AnyNebiusCreds


class NebiusAPIConfig(CoreModel):
    type: Literal["nebius"] = "nebius"
    cloud_id: str
    folder_id: str
    network_id: str
    regions: Optional[List[str]] = None
    creds: AnyNebiusAPICreds


class OCIConfig(CoreModel):
    type: Annotated[Literal["oci"], Field(description="The type of backend")] = "oci"
    creds: Annotated[AnyOCICreds, Field(description="The credentials", discriminator="type")]
    regions: Annotated[
        Optional[List[str]],
        Field(description="The list of OCI regions. Omit to use all regions"),
    ] = None
    compartment_id: Annotated[
        Optional[str],
        Field(
            description=(
                "Compartment where `dstack` will create all resources."
                " Omit to instruct `dstack` to create a new compartment"
            )
        ),
    ] = None


class RunpodConfig(CoreModel):
    type: Literal["runpod"] = "runpod"
    regions: Annotated[
        Optional[List[str]],
        Field(description="The list of RunPod regions. Omit to use all regions"),
    ] = None
    creds: Annotated[AnyRunpodCreds, Field(description="The credentials")]


class TensorDockConfig(CoreModel):
    type: Annotated[Literal["tensordock"], Field(description="The type of backend")] = "tensordock"
    regions: Annotated[
        Optional[List[str]],
        Field(description="The list of TensorDock regions. Omit to use all regions"),
    ] = None
    creds: Annotated[AnyTensorDockCreds, Field(description="The credentials")]


class VastAIConfig(CoreModel):
    type: Annotated[Literal["vastai"], Field(description="The type of backend")] = "vastai"
    regions: Annotated[
        Optional[List[str]],
        Field(description="The list of VastAI regions. Omit to use all regions"),
    ] = None
    creds: Annotated[AnyVastAICreds, Field(description="The credentials")]


class VultrConfig(CoreModel):
    type: Annotated[Literal["vultr"], Field(description="The type of backend")] = "vultr"
    regions: Annotated[
        Optional[List[str]],
        Field(description="The list of Vultr regions. Omit to use all regions"),
    ] = None
    creds: Annotated[AnyVultrCreds, Field(description="The credentials")]


class DstackConfig(CoreModel):
    type: Annotated[Literal["dstack"], Field(description="The type of backend")] = "dstack"


AnyBackendConfig = Union[
    AWSConfig,
    AzureConfig,
    CudoConfig,
    DataCrunchConfig,
    GCPConfig,
    KubernetesConfig,
    LambdaConfig,
    NebiusConfig,
    OCIConfig,
    RunpodConfig,
    TensorDockConfig,
    VastAIConfig,
    VultrConfig,
    DstackConfig,
]

BackendConfig = Annotated[AnyBackendConfig, Field(..., discriminator="type")]


class _BackendConfig(BaseModel):
    __root__: BackendConfig


AnyBackendAPIConfig = Union[
    AWSConfig,
    AzureConfig,
    CudoConfig,
    DataCrunchConfig,
    GCPAPIConfig,
    KubernetesAPIConfig,
    LambdaConfig,
    NebiusAPIConfig,
    OCIConfig,
    RunpodConfig,
    TensorDockConfig,
    VastAIConfig,
    VultrConfig,
    DstackConfig,
]


BackendAPIConfig = Annotated[AnyBackendAPIConfig, Field(..., discriminator="type")]


class _BackendAPIConfig(CoreModel):
    __root__: BackendAPIConfig


class ProjectConfig(CoreModel):
    name: Annotated[str, Field(description="The name of the project")]
    backends: Annotated[
        Optional[List[BackendConfig]], Field(description="The list of backends")
    ] = None


EncryptionKeyConfig = Annotated[AnyEncryptionKeyConfig, Field(..., discriminator="type")]


class EncryptionConfig(CoreModel):
    keys: Annotated[List[EncryptionKeyConfig], Field(description="The encryption keys")]


class ServerConfig(CoreModel):
    projects: Annotated[List[ProjectConfig], Field(description="The list of projects")]
    encryption: Annotated[
        Optional[EncryptionConfig], Field(description="The encryption config")
    ] = None
    default_permissions: Annotated[
        Optional[DefaultPermissions], Field(description="The default user permissions")
    ]


class ServerConfigManager:
    def load_config(self) -> bool:
        self.config = self._load_config()
        return self.config is not None

    async def init_config(self, session: AsyncSession):
        """
        Initializes the default server/config.yml.
        The default config is empty or contains an existing `main` project config.
        """
        # The backends auto init feature via default creds is currently disabled
        # so that the backend configuration is always explicit.
        # Details: https://github.com/dstackai/dstack/issues/1384
        self.config = await self._init_config(session=session, init_backends=False)
        if self.config is not None:
            self._save_config(self.config)

    async def sync_config(self, session: AsyncSession):
        # Disable config.yml sync for https://github.com/dstackai/dstack/issues/815.
        return
        # self.config = await self._init_config(session=session, init_backends=False)
        # if self.config is not None:
        #     self._save_config(self.config)

    async def apply_encryption(self):
        if self.config is None:
            logger.info("No server/config.yml. Skipping encryption configuration.")
            return
        if self.config.encryption is not None:
            encryption_services.init_encryption_keys(self.config.encryption.keys)

    async def apply_config(self, session: AsyncSession, owner: UserModel):
        if self.config is None:
            raise ValueError("Config is not loaded")
        if self.config.default_permissions is not None:
            set_default_permissions(self.config.default_permissions)
        for project_config in self.config.projects:
            await self._apply_project_config(
                session=session, owner=owner, project_config=project_config
            )

    async def _apply_project_config(
        self,
        session: AsyncSession,
        owner: UserModel,
        project_config: ProjectConfig,
    ):
        project = await projects_services.get_project_model_by_name(
            session=session,
            project_name=project_config.name,
        )
        if not project:
            await projects_services.create_project_model(
                session=session, owner=owner, project_name=project_config.name
            )
            project = await projects_services.get_project_model_by_name_or_error(
                session=session, project_name=project_config.name
            )
        backends_to_delete = set(backends_services.list_available_backend_types())
        for backend_config in project_config.backends or []:
            config_info = config_to_internal_config(backend_config)
            backend_type = BackendType(config_info.type)
            backends_to_delete.difference_update([backend_type])
            try:
                current_config_info = await backends_services.get_config_info(
                    project=project,
                    backend_type=backend_type,
                )
            except BackendNotAvailable:
                logger.warning(
                    "Backend %s not available and won't be configured."
                    " Check that backend dependencies are installed.",
                    backend_type.value,
                )
                continue
            if config_info == current_config_info:
                continue
            backend_exists = any(backend_type == b.type for b in project.backends)
            try:
                # current_config_info may be None if backend exists
                # but it's config is invalid (e.g. cannot be decrypted).
                # Update backend in this case.
                if current_config_info is None and not backend_exists:
                    await backends_services.create_backend(
                        session=session, project=project, config=config_info
                    )
                else:
                    await backends_services.update_backend(
                        session=session, project=project, config=config_info
                    )
            except Exception as e:
                logger.warning("Failed to configure backend %s: %s", config_info.type, e)
        await delete_backends_safe(
            session=session,
            project=project,
            backends_types=list(backends_to_delete),
            error=False,
        )

    async def _init_config(
        self, session: AsyncSession, init_backends: bool
    ) -> Optional[ServerConfig]:
        project = await projects_services.get_project_model_by_name(
            session=session,
            project_name=settings.DEFAULT_PROJECT_NAME,
        )
        if project is None:
            return None
        # Force project reload to reflect updates when syncing
        await session.refresh(project)
        backends = []
        for backend_type in backends_services.list_available_backend_types():
            config_info = await backends_services.get_config_info(
                project=project, backend_type=backend_type
            )
            if config_info is not None:
                backends.append(internal_config_to_config(config_info))
        if init_backends and len(backends) == 0:
            backends = await self._init_backends(session=session, project=project)
        return ServerConfig(
            projects=[ProjectConfig(name=settings.DEFAULT_PROJECT_NAME, backends=backends)],
            encryption=EncryptionConfig(keys=[]),
            default_permissions=None,
        )

    async def _init_backends(
        self, session: AsyncSession, project: ProjectModel
    ) -> List[BackendConfig]:
        backends = []
        for backend_type in backends_services.list_available_backend_types():
            configurator = backends_services.get_configurator(backend_type)
            if configurator is None:
                continue
            config_infos = await run_async(configurator.get_default_configs)
            for config_info in config_infos:
                try:
                    await backends_services.create_backend(
                        session=session, project=project, config=config_info
                    )
                    backends.append(internal_config_to_config(config_info))
                    break
                except Exception as e:
                    logger.debug("Failed to configure backend %s: %s", config_info.type, e)
        return backends

    def _load_config(self) -> Optional[ServerConfig]:
        try:
            with open(settings.SERVER_CONFIG_FILE_PATH) as f:
                content = f.read()
        except OSError:
            return
        config_dict = yaml.load(content, yaml.FullLoader)
        return ServerConfig.parse_obj(config_dict)

    def _save_config(self, config: ServerConfig):
        with open(settings.SERVER_CONFIG_FILE_PATH, "w+") as f:
            f.write(config_to_yaml(config))


async def get_backend_config_yaml(
    project: ProjectModel, backend_type: BackendType
) -> BackendInfoYAML:
    config_info = await backends_services.get_config_info(
        project=project, backend_type=backend_type
    )
    if config_info is None:
        raise ResourceNotExistsError()
    config = internal_config_to_config(config_info)
    config_yaml = config_to_yaml(config)
    return BackendInfoYAML(
        name=backend_type,
        config_yaml=config_yaml,
    )


async def create_backend_config_yaml(
    session: AsyncSession,
    project: ProjectModel,
    config_yaml: str,
):
    backend_config = config_yaml_to_backend_config(config_yaml)
    config_info = config_to_internal_config(backend_config)
    await backends_services.create_backend(session=session, project=project, config=config_info)


async def update_backend_config_yaml(
    session: AsyncSession,
    project: ProjectModel,
    config_yaml: str,
):
    backend_config = config_yaml_to_backend_config(config_yaml)
    config_info = config_to_internal_config(backend_config)
    await backends_services.update_backend(session=session, project=project, config=config_info)


server_config_manager = ServerConfigManager()


def internal_config_to_config(config_info: AnyConfigInfoWithCreds) -> BackendConfig:
    backend_config = _BackendConfig.parse_obj(config_info.dict(exclude={"locations"}))
    if config_info.type == "azure":
        backend_config.__root__.regions = config_info.locations
    return backend_config.__root__


class _ConfigInfoWithCreds(CoreModel):
    __root__: Annotated[AnyConfigInfoWithCreds, Field(..., discriminator="type")]


def config_to_internal_config(
    backend_config: Union[BackendConfig, BackendAPIConfig],
) -> AnyConfigInfoWithCreds:
    backend_config_dict = backend_config.dict()
    # Allow to not specify networking
    if backend_config.type == "kubernetes":
        if backend_config.networking is None:
            backend_config_dict["networking"] = {}
    if backend_config.type == "azure":
        backend_config_dict["locations"] = backend_config_dict["regions"]
        del backend_config_dict["regions"]
    config_info = _ConfigInfoWithCreds.parse_obj(backend_config_dict)
    return config_info.__root__


def config_yaml_to_backend_config(config_yaml: str) -> BackendAPIConfig:
    try:
        config_dict = yaml.load(config_yaml, yaml.FullLoader)
    except yaml.YAMLError:
        raise ServerClientError("Error parsing YAML")
    try:
        backend_config = _BackendAPIConfig.parse_obj(config_dict).__root__
    except ValidationError as e:
        raise ServerClientError(str(e))
    return backend_config


def config_to_yaml(config: CoreModel) -> str:
    return yaml.dump(config.dict(exclude_none=True), sort_keys=False)


def _fill_data(values: dict):
    if values.get("data") is not None:
        return values
    if "filename" not in values:
        raise ValueError()
    try:
        with open(Path(values["filename"]).expanduser()) as f:
            values["data"] = f.read()
    except OSError:
        raise ValueError(f"No such file {values['filename']}")
    return values
