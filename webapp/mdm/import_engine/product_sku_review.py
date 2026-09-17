"""商品导入 V2 Warning acknowledgement using the proven review lifecycle."""

from .customer_review import CustomerReviewService
from .workflow import WorkflowError, get_batch


class GoodsReviewService(CustomerReviewService):
    """商品导入 scoped review service (rows are SKU rows with a Product plan)."""

    entity_type = "PRODUCT_SKU"
    entity_label = "商品导入"

    def acknowledge(self, batch_id, payload, operator_id):
        batch = get_batch(self.session, batch_id)
        if batch.error_rows:
            raise WorkflowError(
                "MDM_IMPORT_REVIEW_INCOMPLETE",
                "商品导入 Warning cannot be acknowledged while ERROR rows exist",
                409,
                {"error": batch.error_rows},
            )
        return super().acknowledge(batch_id, payload, operator_id)
