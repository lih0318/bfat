"""Domain and application exceptions."""


class BFATException(Exception):
    """Base BFAT exception."""

    pass


class ExecutionError(BFATException):
    """Order execution failed."""

    pass


class RiskViolationError(BFATException):
    """Risk limit violated."""

    pass
