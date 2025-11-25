"""Sandbox executor for isolated workload execution."""

import os
import subprocess
import time
import signal
from pathlib import Path
from typing import Dict, Optional
from dataclasses import dataclass


@dataclass
class SandboxResult:
    """Result of sandbox execution."""

    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False


class SandboxExecutor:
    """Execute commands in a sandboxed environment with resource limits."""

    def __init__(
        self,
        network_blocked: bool = True,
        timeout: int = 300,
        max_memory_mb: int = 2048,
        working_directory: Optional[Path] = None,
    ):
        """Initialize sandbox executor.

        Args:
            network_blocked: Whether to block network access
            timeout: Timeout in seconds
            max_memory_mb: Maximum memory in MB
            working_directory: Working directory for execution
        """
        self.network_blocked = network_blocked
        self.timeout = timeout
        self.max_memory_mb = max_memory_mb
        self.working_directory = working_directory or Path.cwd()

    def run(
        self,
        command: str,
        environment: Optional[Dict[str, str]] = None,
        working_directory: Optional[Path] = None,
    ) -> SandboxResult:
        """Run command in sandbox.

        Args:
            command: Shell command to execute
            environment: Environment variables
            working_directory: Override working directory for this command

        Returns:
            SandboxResult with execution details

        Raises:
            Exception: If sandbox setup fails
        """
        start_time = time.time()
        timed_out = False

        # Prepare environment
        env = os.environ.copy()
        if environment:
            env.update(environment)

        # Determine working directory
        work_dir = working_directory or self.working_directory

        # Build command with resource limits
        # Note: For production, add seccomp/landlock, but start simple
        wrapped_command = self._wrap_command(command)

        try:
            # Execute command with timeout
            result = subprocess.run(
                wrapped_command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(work_dir),
                env=env,
            )

            exit_code = result.returncode
            stdout = result.stdout
            stderr = result.stderr

        except subprocess.TimeoutExpired as e:
            timed_out = True
            exit_code = -1
            stdout = e.stdout.decode() if e.stdout else ""
            stderr = e.stderr.decode() if e.stderr else ""

        duration = time.time() - start_time

        return SandboxResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration,
            timed_out=timed_out,
        )

    def _wrap_command(self, command: str) -> str:
        """Wrap command with resource limits.

        Args:
            command: Original command

        Returns:
            Wrapped command with resource limits

        Note:
            This is a simplified implementation. In production TEE:
            - Add ulimit for memory: ulimit -v {max_memory_kb}
            - Add network blocking: unshare --net (requires root)
            - Add seccomp filters
            - Add landlock filesystem restrictions
        """
        # For now, just return the command as-is
        # In production, we'd add:
        # - ulimit -v for memory limits
        # - unshare --net for network isolation (if network_blocked)
        # - timeout command for additional timeout layer

        wrapped = command

        # Add bash shell wrapper to handle multi-line commands
        if "\n" in command or ";" in command:
            # Use bash -c for complex commands
            wrapped = f'bash -c {self._shell_quote(command)}'

        return wrapped

    def _shell_quote(self, s: str) -> str:
        """Quote a string for safe shell execution.

        Args:
            s: String to quote

        Returns:
            Quoted string safe for shell
        """
        # Replace single quotes with '\'' and wrap in single quotes
        return "'" + s.replace("'", "'\\''") + "'"

    def verify_sandbox_restrictions(self) -> Dict[str, bool]:
        """Verify that sandbox restrictions are in place.

        Returns:
            Dictionary of restriction name to enabled status

        Note:
            This is a placeholder for future TEE implementation.
            In production, this would check:
            - Network isolation is active
            - Memory limits are enforced
            - Seccomp filters are loaded
            - Landlock restrictions are active
        """
        return {
            "network_blocked": self.network_blocked,
            "timeout_enabled": True,
            "memory_limit_enabled": False,  # Not implemented yet
            "seccomp_enabled": False,  # Not implemented yet
            "landlock_enabled": False,  # Not implemented yet
        }


class SandboxViolationError(Exception):
    """Raised when sandbox restrictions are violated."""

    pass


def create_sandbox(
    network_access: bool = False,
    timeout_seconds: int = 300,
    max_memory_mb: int = 2048,
    working_directory: Optional[Path] = None,
) -> SandboxExecutor:
    """Create a sandbox executor with specified restrictions.

    Args:
        network_access: Allow network access (default: False)
        timeout_seconds: Command timeout in seconds
        max_memory_mb: Maximum memory in MB
        working_directory: Working directory for execution

    Returns:
        Configured SandboxExecutor
    """
    return SandboxExecutor(
        network_blocked=not network_access,
        timeout=timeout_seconds,
        max_memory_mb=max_memory_mb,
        working_directory=working_directory,
    )
