"""Background task supervision.

:mod:`shoreguard.tasks.supervisor` provides the generic
:class:`~shoreguard.tasks.supervisor.PeriodicTask` /
:class:`~shoreguard.tasks.supervisor.TaskSupervisor` machinery;
:mod:`shoreguard.tasks.definitions` declares ShoreGuard's concrete
background tasks (cleanup, gateway health, discovery, drift
detection, cert rotation).
"""

from shoreguard.tasks.supervisor import PeriodicTask, TaskSupervisor

__all__ = ("PeriodicTask", "TaskSupervisor")
