class ContextBudgetError(ValueError):
    """The same work must be retried in smaller source batches, never truncated."""
