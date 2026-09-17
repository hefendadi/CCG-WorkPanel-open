"""Product Import V1 user-facing routes and Chinese vocabulary."""

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
    "home": "/mdm/product-import-center",
    "upload": "/mdm/product-import-center/upload",
    "review": "/mdm/product-import-center/batches/{batch_id}/review",
    "commit": "/mdm/product-import-center/batches/{batch_id}/commit",
}

ISSUE_COPY = {
    "MDM_IMPORT_FILE_INVALID": "文件不是有效的 Excel 工作簿，请重新上传",
    "MDM_IMPORT_SHEET_INVALID": "工作表不符合 Product 导入模板，请使用正确模板",
    "MDM_IMPORT_REQUIRED_FIELD_MISSING": "必填字段为空，请在 Excel 中补充后重新上传",
    "MDM_IMPORT_VALUE_INVALID": "字段值不符合格式要求，请在 Excel 中修正后重新上传",
    "MDM_IMPORT_CODE_NOT_STRING_SAFE": "Product Code 无法保真，请在 Excel 中修改后重新上传",
    "MDM_IMPORT_DUPLICATE_UNIQUE_CODE": "文件内 Product Code 重复，请修正后重新上传",
    "MDM_IMPORT_PRODUCT_CODE_CONFLICT": "Product Code 已对应其他 Product 名称，禁止通过导入修改已有 Product",
    "MDM_IMPORT_PRODUCT_NAME_CONFLICT": "相同 Product 名称已使用其他 Code，确认后仍作为新 Product 创建",
    "MDM_IMPORT_PRODUCT_EXISTING": "Product Code 与名称已存在，仅展示，不会更新或重复创建",
}

COPY_UPLOAD_VALIDATED = "Product Excel 已完成检查"
COPY_UPLOAD_FILE_REJECTED = "文件无法进入导入流程，请修正后重新上传"
COPY_UPLOAD_SERVER_ERROR = "上传或检查失败，请稍后重试"
COPY_UPLOAD_NOT_XLSX = "请选择 .xlsx 文件"
COPY_REVIEW_LOAD_ERROR = "Product 导入明细加载失败，请稍后重试"
COPY_REVIEW_NOT_FOUND = "Product 导入批次不存在"
COPY_ACKNOWLEDGE_SUCCESS = "已确认该 Product 同名提示"
COPY_ACKNOWLEDGE_STALE = "批次已变更，请刷新后重试"
COPY_ACKNOWLEDGE_ERROR = "确认失败，请刷新后重试"
COPY_ERROR_FIRST_BLOCKED = "请修改 Excel 后重新上传"
COPY_ERROR_FIRST_NO_CONFIRM = "存在错误时不能确认其他提示"
COPY_COMMIT_NOT_READY = "当前批次尚不能提交"
COPY_COMMIT_FORBIDDEN = "需要 MDM EDIT 权限才能提交 Product 导入"
COPY_COMMIT_SERVER_ERROR = "提交失败，本批次没有产生部分写入"
