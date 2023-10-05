import os
import logging
import subprocess
import copy
from typing import Tuple, List, Dict, Optional

from ray._private.runtime_env.context import RuntimeEnvContext
from ray._private.runtime_env.plugin import RuntimeEnvPlugin
from ray._private.runtime_env.packaging import (
    delete_package,
    get_local_dir_from_uri,
)
from ray._private.utils import (
    get_directory_size_bytes,
    try_to_create_directory,
)

default_logger = logging.getLogger(__name__)

NSIGHT_DEFEAULT_CONFIG = {
    "-t": "cuda,cudnn,cublas,nvtx",
    "-o": "'worker_process_%p'",
    "--cudabacktrace": "all",
    "--stop-on-exit": "true",
}


def check_nsight_script(nsight_config: Dict[str, str]) -> Tuple[bool, str]:
    """
    Function to check if the provided nsight options are
    valid nsys profile options and if nsys profile is installed

    The function returns:
    - bool: True if the nsight_config is valid option
    - str: error message if the nsight_config is invalid
    """

    # use empty as nsight report test filename

    default_logger.info(f"nsight: {nsight_config}")

    nsight_config_copy = copy.deepcopy(nsight_config)
    default_logger.info(f"nsight: {nsight_config_copy}")
    nsight_config_copy["-o"] = "empty"
    nsight_cmd = parse_nsight_config(nsight_config_copy)
    try:
        nsight_cmd = nsight_cmd + ["python", "-c", '""']
        result = subprocess.run(
            nsight_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode == 0:
            subprocess.run(["rm", "-f", "empty.nsight-rep"])
            return True, None
        else:
            error_msg = (
                result.stderr.strip()
                if result.stderr.strip() != ""
                else result.stdout.strip()
            )
            return False, error_msg
    except FileNotFoundError:
        return False, ("nsight is not installed")


def parse_nsight_config(nsight_config: Dict[str, str]) -> List[str]:
    """
    Function to convert dictionary of nsight options into
    nsight command line

    The function returns:
    - List[str]: nsys profile cmd line split into list of str
    """
    nsight_cmd = ["nsys", "profile"]
    for option, option_val in nsight_config.items():
        if option[:2] == "--":
            nsight_cmd.append(f"{option}={option_val}")
        elif option[0] == "-":
            nsight_cmd += [option, option_val]
    return nsight_cmd


class NsightPlugin(RuntimeEnvPlugin):
    name = "nsight"

    def __init__(self, resources_dir: str):
        self.nsight_cmd = []
        self._resources_dir = os.path.join(resources_dir, "nsight")
        try_to_create_directory(self._resources_dir)

        # replace this with better way to get logs dir
        session_dir, runtime_dir = os.path.split(resources_dir)
        self._logs_dir = os.path.join(session_dir + "/logs/nsight")
        try_to_create_directory(self._logs_dir)
        default_logger.info("Directory created")

    def delete_uri(
        self, uri: str, logger: Optional[logging.Logger] = default_logger
    ) -> int:
        """Delete URI and return the number of bytes deleted."""
        logger.info("Got request to delete nsight URI %s", uri)
        local_dir = get_local_dir_from_uri(uri, self._resources_dir)
        local_dir_size = get_directory_size_bytes(local_dir)

        # move package to head node
        deleted = delete_package(uri, self._resources_dir)
        if not deleted:
            logger.warning(f"Tried to delete nonexistent URI: {uri}.")
            return 0

        return local_dir_size

    async def create(
        self,
        uri: Optional[str],
        runtime_env: "RuntimeEnv",  # noqa: F821
        context: RuntimeEnvContext,
        logger: logging.Logger = default_logger,
    ) -> int:
        default_logger.info("Creating nsight runtime env")

        nsight_config = runtime_env.get("nsight")
        if not nsight_config:
            default_logger.info("Return 0")
            return 0

        if isinstance(nsight_config, str):
            if nsight_config == "default":
                nsight_config = NSIGHT_DEFEAULT_CONFIG
            else:
                raise RuntimeError(
                    f"Unsupported nsight config: {nsight_config}. "
                    "The supported config is 'default' or "
                    "Dictionary of nsight options"
                )

        is_valid_nsight_cmd, error_msg = check_nsight_script(nsight_config)
        if not is_valid_nsight_cmd:
            logger.warning(error_msg)
            raise RuntimeError(
                "nsight profile failed to run with the following "
                f"error message:\n {error_msg}"
            )
        # add set output path to logs dir

        output = nsight_config.get(
            "-o", NSIGHT_DEFEAULT_CONFIG["-o"]
        )

        default_logger.info(f"nsight: setting -o to {output}")
        default_logger.info(f"nsight: logs dir: {self._logs_dir}")
        default_logger.info(f"nsight: previous dir: {self._logs_dir}/{output}")

        nsight_config["-o"] = output
        self.nsight_cmd = parse_nsight_config(nsight_config)
        return 0

    def modify_context(
        self,
        uris: List[str],
        runtime_env: "RuntimeEnv",  # noqa: F821
        context: RuntimeEnvContext,
        logger: Optional[logging.Logger] = default_logger,
    ):
        logger.info("Running nsight profiler")
        context.py_executable = " ".join(self.nsight_cmd) + " python"
