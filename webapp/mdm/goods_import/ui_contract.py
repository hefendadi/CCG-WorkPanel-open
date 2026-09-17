"""商品导入 V2 user-facing routes and Chinese vocabulary.

Reuses the Customer Import V1 Review page model (batch list / upload /
four tabs / commit) frozen by docs/import-contracts.md, adapted to the merged Product+SKU
workbook.
"""

from webapp.mdm.customer_import.ui_contract import (
    BATCH_ACTION_COPY,
    BATCH_STATUS_COPY,
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROW_STATUS_COPY,
    TAB_LABELS,
    TAB_STATUS_MAP,
    REVIEWABLE_STATUSES,
)

PAGE_ROUTES = {
    "home": "/mdm/goods-import-center",
    "upload": "/mdm/goods-import-center/upload",
    "review": "/mdm/goods-import-center/batches/{batch_id}/review",
    "commit": "/mdm/goods-import-center/batches/{batch_id}/commit",
}

ISSUE_COPY = {
    "MDM_IMPORT_FILE_INVALID": "文件不是有效的 Excel 工作簿，请重新上传",
    "MDM_IMPORT_SHEET_INVALID": "工作表不符合商品导入模板，请使用冻结模板",
    "MDM_IMPORT_REQUIRED_FIELD_MISSING": "必填字段（商品编码/物料名称/合并编码）为空，请在 Excel 中补充后重新上传",
    "MDM_IMPORT_VALUE_INVALID": "字段值不符合格式要求，请在 Excel 中修正后重新上传",
    "MDM_IMPORT_CODE_NOT_STRING_SAFE": "商品编码或合并编码无法保真，请在 Excel 中修改后重新上传",
    "MDM_IMPORT_DATE_INVALID": "创建日期格式无法识别，请修改 Excel 后重新上传",
    "MDM_IMPORT_DUPLICATE_UNIQUE_CODE": "文件内商品编码（SKU Code）重复，请修正后重新上传",
    "MDM_IMPORT_SKU_CODE_CONFLICT": "商品编码已存在于主数据但名称不同，禁止通过导入修改已有 SKU 名称",
    "MDM_IMPORT_SKU_PRODUCT_CONFLICT": "商品编码已归属其他 Product，禁止通过导入隐式调整归属",
    "MDM_IMPORT_PRODUCT_ATTR_CONFLICT": "同一合并编码对应的 Product 属性相互矛盾，无法本批创建该 Product，请修正后重新上传",
    "MDM_IMPORT_PRODUCT_NAME_REQUIRED": "合并编码对应 Product 尚不存在，且缺少显式 Product 名称或 SKU Code == Product Code 的代表行，无法确定 Product 名称，请修正后重新上传",
    "MDM_IMPORT_REFERENCE_UNRESOLVED": "合并编码无法唯一解析到 Product，请在 Excel 中修正后重新上传",
    "MDM_IMPORT_INACTIVE_REFERENCE": "合并编码对应 Product 已停用，请改用有效 Product 后重新上传",
    "MDM_IMPORT_SKU_EXISTING": "商品编码与名称已存在，仅展示差异，不会更新或重复创建",
    "MDM_IMPORT_SKU_NAME_FORMAT_DIFFERENCE": "SKU 名称仅存在确定性格式差异（空白/全角半角等），按同名处理，Master 保持不变",
    "MDM_IMPORT_SOURCE_DATE_LABEL": "创建日期为月份业务标签，已按空值处理",
}

COPY_UPLOAD_VALIDATED = "商品 Excel 已完成检查"
COPY_UPLOAD_FILE_REJECTED = "文件无法进入导入流程，请修正后重新上传"
COPY_UPLOAD_SERVER_ERROR = "上传或检查失败，请稍后重试"
COPY_UPLOAD_NOT_XLSX = "请选择 .xlsx 文件"
COPY_REVIEW_LOAD_ERROR = "商品导入明细加载失败，请稍后重试"
COPY_REVIEW_NOT_FOUND = "商品导入批次不存在"
COPY_ACKNOWLEDGE_SUCCESS = "已确认该商品导入提示"
COPY_ACKNOWLEDGE_STALE = "批次已变更，请刷新后重试"
COPY_ACKNOWLEDGE_ERROR = "确认失败，请刷新后重试"
COPY_ERROR_FIRST_BLOCKED = "请修改 Excel 后重新上传"
COPY_ERROR_FIRST_NO_CONFIRM = "存在错误时不能确认其他提示"
COPY_COMMIT_NOT_READY = "当前批次尚不能提交"
COPY_COMMIT_FORBIDDEN = "需要 MDM EDIT 权限才能提交商品导入"
COPY_COMMIT_SERVER_ERROR = "提交失败，本批次没有产生部分写入"
