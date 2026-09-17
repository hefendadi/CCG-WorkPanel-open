"""SKU Import V1 Warning acknowledgement using the proven review lifecycle."""

from .customer_review import CustomerReviewService
from .workflow import WorkflowError, get_batch


class SKUReviewService(CustomerReviewService):
    """SKU-scoped review service; SKU V1 produces no DECIDE Warnings, so the
    acknowledge endpoint exists only for interface parity with Customer and
    Product Import (a non-Warning finding is rejected by the base service)."""

    entity_type = "SKU"
    entity_label = "SKU"

    def acknowledge(self, batch_id, payload, operator_id):
        batch = get_batch(self.session, batch_id)
        if batch.error_rows:
            raise WorkflowError(
                "MDM_IMPORT_REVIEW_INCOMPLETE",
                "SKU Warning cannot be acknowledged while ERROR rows exist",
                409,
                {"error": batch.error_rows},
            )
        return super().acknowledge(batch_id, payload, operator_id)
