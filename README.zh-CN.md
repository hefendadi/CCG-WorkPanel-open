[English](README.md) | [简体中文](README.zh-CN.md)

# CCG WorkPanel

面向快消行业的销售与运营工作台

## 项目介绍

CCG WorkPanel 是一个面向 FMCG（快消品）销售与运营场景的开源工作台。
它以 MDM（主数据管理）为基础，将销售实绩、数据导入、库存、来货、
人工预测和订货流程组织在同一个工作空间中。

项目可在本地独立运行。随项目提供的业务数据均为从零生成的合成演示数据，
方便业务人员了解流程，也方便开发者验证系统行为。

## 解决的问题

快消业务中的客户、商品、SKU、渠道、销售实绩、库存、来货、预测和订货，
往往分别存在于不同的 Excel、ERP 导出文件和人工流程中。随着业务规模扩大，
困难不再只是“有没有数据”，而是如何让这些数据使用同一套主数据、
同一套业务关系和同一套可追溯逻辑。

CCG WorkPanel 用统一主数据和明确的数据来源版本连接这些流程，
让不同模块围绕同一份业务事实开展工作，并保留数据变化的记录。
它定位为轻量级销售与运营工作台，并非完整 ERP 或完整 S&OP 平台。

## 核心模块

- **账号与权限：** 按模块设置 NONE / VIEW / EDIT 权限，并提供账号管理。
- **主数据管理（MDM）：** 维护客户、商品、SKU、渠道、销售员及相关基础资料。
- **导入中心（Import Center）：** 完成主数据文件的解析、校验、审核和原子提交。
- **销售实绩（ACT Sales）：** 支持数据映射、预览、发布及数量指标看板。
- **订货与预测（Ordering / Forecast）：** 围绕库存、来货和计划周期，
  管理人工预测版本、最终订货及锁定周期的历史。
- **工作台入口（Portal）：** 根据账号权限展示概览和模块导航。

## 系统架构

MDM、销售实绩和订货模块共享业务数据与导入记录，账号及权限单独存储。
前端采用原生 HTML、CSS 和 JavaScript；FastAPI 组合各业务模块的路由；
SQLite 保存账号与权限，SQLAlchemy / MySQL 保存业务数据与导入记录。

Alembic 以唯一公开基线 `0001_public_baseline` 初始化业务数据库，
包含关系约束和触发器。计划周期需要明确采用数据来源版本，
预测以不可变版本保存，锁定周期保留历史记录。
项目不包含历史销售日报模块。

详细说明见[系统架构](docs/architecture.md)和[导入约定](docs/import-contracts.md)。

## 本地快速启动

需要安装 Docker 和 Compose。请在仓库根目录执行：

```sh
docker compose -p ccgtools-open-demo -f deploy/local/compose.yml up --build -d --wait
```

启动后访问 <http://127.0.0.1:18080/login>。
独立 MySQL 服务仅绑定 `127.0.0.1:13306`，用于本地测试。

首次初始化按以下顺序执行：

1. 根据公开 Alembic 基线创建 MySQL 业务库结构。
2. 创建 SQLite 账号库结构。
3. 执行账号权限迁移，并核对迁移标记。
4. 写入合成演示账号及最终权限，再写入合成业务数据。
5. 启动应用。

应用启动和登录时会再次核对已执行的账号迁移，不会重置既有权限。
无需手动修改 SQLite。应用、数据初始化和数据库迁移使用同一个本地构建镜像。

可覆盖的环境变量见 [`.env.example`](deploy/local/.env.example)。
Compose 读取主机环境变量，不会自动加载这个示例文件。
其他本地开发方式见[本地开发说明](docs/local-development.md)。

### 重置本地 Demo

以下操作只删除此 Compose 项目的虚构数据卷，并重新初始化：

```sh
docker compose -p ccgtools-open-demo -f deploy/local/compose.yml down --volumes
docker compose -p ccgtools-open-demo -f deploy/local/compose.yml up --build -d --wait
```

## 演示账号

三个账号的密码均为 `demo-only-change-me`。
以下账号仅用于本机 Demo 环境，
请勿在任何实际部署环境中复用这些凭据。

| 账号 | 主数据管理 | 销售实绩 | 订货 / 预测 | 账号管理 |
| --- | --- | --- | --- | --- |
| 演示管理员（`demo_admin`） | EDIT | EDIT | EDIT | EDIT |
| 演示操作员（`demo_operator`） | EDIT | EDIT | EDIT | NONE |
| 演示查看者（`demo_viewer`） | VIEW | VIEW | VIEW | NONE |

## 合成演示数据

执行 `python -m demo.generate_data`，可重复生成 XLSX、CSV 和 JSON 格式的
演示文件，存放于 `webapp/tests/fixtures/synthetic/`。
`python -m demo.seed` 只向明确启用的演示数据库写入数据。

这些名称、编码、数量和业务关系均为虚构，不是对真实业务记录进行脱敏。
演示采用四个月的计划窗口，明确区分仓库可用范围，
并提供可配置的销售下降确认门槛
（`CCGTOOLS_SALES_DROP_THRESHOLD`，演示默认值为 `0.8`）。

## 测试

```sh
docker compose -p ccgtools-open-demo -f deploy/local/compose.yml run --rm tests
node --test webapp/tests/public_frontend_smoke.cjs
python -m demo.security_scan
```

测试覆盖空 SQLite 账号库初始化、账号迁移与权限、MySQL 库结构及约束和触发器、
合成数据导入、销售实绩发布、预测与订货、周期锁定、登录、Portal 和前端契约。
MySQL 测试不得静默回退到 SQLite。

参与开发的约定见[贡献指南](CONTRIBUTING.md)。

## 当前限制

- 界面以中文为主。导入适配器约定的是演示工作簿格式，不代表兼容所有 ERP。
- 已提供订货相关导入服务与最终订货核心逻辑，但部分操作尚无浏览器工作流。
  数据初始化工具展示了这些已支持的核心流程。
- 预测由人工提供；当前公开契约采用四个月的预测窗口。
- 本地 HTTP 默认不启用 Cookie Secure。使用 HTTPS 时，
  需设置 `CCGTOOLS_PORTAL_COOKIE_SECURE=true`。
  HttpOnly 和 SameSite=Lax 保持启用。

## 许可证

License: Apache-2.0。完整条款见 [LICENSE](LICENSE)。
