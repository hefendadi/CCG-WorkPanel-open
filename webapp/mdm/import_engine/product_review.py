"""Product Import V1 Warning acknowledgement using the proven review lifecycle."""

from .customer_review import CustomerReviewService
from .workflow import WorkflowError, get_batch


class ProductReviewService(CustomerReviewService):
    """Product-scoped review service; Product has no value-resolution actions."""

    entity_type = "PRODUCT"
    entity_label = "Product"

    def acknowledge(self, batch_id, payload, operator_id):
        batch = get_batch(self.session, batch_id)
        if batch.error_rows:
            raise WorkflowError(
                "MDM_IMPORT_REVIEW_INCOMPLETE",
                "Product Warning cannot be acknowledged while ERROR rows exist",
                409,
                {"error": batch.error_rows},
            )
        return super().acknowledge(batch_id, payload, operator_id)
