"""Safe public exceptions; raw source values and credentials are not logged."""


class GeoFlowError(Exception):
    """Base pipeline error."""


class ConfigError(GeoFlowError):
    """Configuration is invalid."""


class IngestionError(GeoFlowError):
    """Input cannot be read without losing records."""


class QualityError(GeoFlowError):
    """A configured quality gate failed."""


class RunLockedError(GeoFlowError):
    """Another process owns this run's lock."""


class PipelineInterrupted(GeoFlowError):
    """Controlled interruption; allow transaction and report cleanup."""
