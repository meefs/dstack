from base64 import b64decode
from enum import Enum
from typing import Dict, List, Optional, Union

from pydantic import Field, validator
from typing_extensions import Annotated

from dstack._internal.core.models.common import CoreModel, NetworkMode
from dstack._internal.core.models.repos.remote import RemoteRepoCreds
from dstack._internal.core.models.runs import ClusterInfo, JobSpec, JobStatus, RunSpec
from dstack._internal.core.models.volumes import InstanceMountPoint, VolumeMountPoint


class JobStateEvent(CoreModel):
    timestamp: int
    state: JobStatus
    termination_reason: Optional[str] = None
    termination_message: Optional[str] = None


class LogEvent(CoreModel):
    timestamp: int  # milliseconds
    message: bytes

    @validator("message", pre=True)
    def decode_message(cls, v: Union[str, bytes]) -> bytes:
        if isinstance(v, str):
            return b64decode(v)
        return v


class PullResponse(CoreModel):
    job_states: List[JobStateEvent]
    job_logs: List[LogEvent]
    runner_logs: List[LogEvent]
    last_updated: int
    no_connections_secs: Optional[int] = None  # Optional for compatibility with old runners


class SubmitBody(CoreModel):
    run_spec: Annotated[
        RunSpec,
        Field(
            include={
                "run_name",
                "repo_id",
                "repo_data",
                "configuration",
                "configuration_path",
            }
        ),
    ]
    job_spec: Annotated[
        JobSpec,
        Field(
            include={
                "replica_num",
                "job_num",
                "jobs_per_replica",
                "user",
                "commands",
                "entrypoint",
                "env",
                "gateway",
                "single_branch",
                "max_duration",
                "ssh_key",
                "working_dir",
            }
        ),
    ]
    cluster_info: Annotated[Optional[ClusterInfo], Field(include=True)]
    secrets: Annotated[Optional[Dict[str, str]], Field(include=True)]
    repo_credentials: Annotated[Optional[RemoteRepoCreds], Field(include=True)]


class HealthcheckResponse(CoreModel):
    service: str
    version: str


class GPUMetrics(CoreModel):
    gpu_memory_usage_bytes: int
    gpu_util_percent: int


class MetricsResponse(CoreModel):
    timestamp_micro: int
    cpu_usage_micro: int
    memory_usage_bytes: int
    memory_working_set_bytes: int
    gpus: List[GPUMetrics]


class ShimVolumeInfo(CoreModel):
    backend: str
    name: str
    volume_id: str
    init_fs: bool
    device_name: Optional[str] = None


class PortMapping(CoreModel):
    host: int
    container: int


class TaskStatus(str, Enum):
    PENDING = "pending"
    PREPARING = "preparing"
    PULLING = "pulling"
    CREATING = "creating"
    RUNNING = "running"
    TERMINATED = "terminated"


class TaskInfoResponse(CoreModel):
    id: str
    status: TaskStatus
    termination_reason: str
    termination_message: str
    # default value for backward compatibility with 0.18.34, could be removed after a few releases
    ports: Optional[list[PortMapping]] = []


class TaskSubmitRequest(CoreModel):
    id: str
    name: str
    registry_username: str
    registry_password: str
    image_name: str
    container_user: str
    privileged: bool
    gpu: int
    cpu: float
    memory: int
    shm_size: int
    network_mode: NetworkMode
    volumes: list[ShimVolumeInfo]
    volume_mounts: list[VolumeMountPoint]
    instance_mounts: list[InstanceMountPoint]
    host_ssh_user: str
    host_ssh_keys: list[str]
    container_ssh_keys: list[str]


class TaskTerminateRequest(CoreModel):
    termination_reason: str
    termination_message: str
    timeout: int


class LegacySubmitBody(CoreModel):
    username: str
    password: str
    image_name: str
    privileged: bool
    container_name: str
    container_user: str
    shm_size: int
    public_keys: List[str]
    ssh_user: str
    ssh_key: str
    mounts: List[VolumeMountPoint]
    volumes: List[ShimVolumeInfo]
    instance_mounts: List[InstanceMountPoint]


class LegacyStopBody(CoreModel):
    force: bool = False


class JobResult(CoreModel):
    reason: str
    reason_message: str


class LegacyPullResponse(CoreModel):
    state: str
    result: Optional[JobResult]
