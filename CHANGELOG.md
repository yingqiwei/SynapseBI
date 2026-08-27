# Changelog

## [1.0.0] - 2026-08

### 结构
- 采用 src 布局：业务代码迁移至 `src/synapsebi/{etl,retrieval,frontend}`
- 保留 `module_*` 兼容导入层，旧路径无需改动
- 新增企业级工程化脚手架（pyproject / Makefile / CI / docs / scripts / .env.example）

### 修复
- Excel 布尔列转换：正确处理 pandas 读出的 `1.0/0.0` 浮点值
- PDF 解析：修正 `get_text("blocks")` 字段顺序，文本块不再被误判为图片
- SQLGuard：识别 WITH CTE 别名，表名白名单不再误杀
- Text-to-SQL：样本 JSON 序列化兜底、Ollama `/v1` 地址兼容、执行失败纠错重试、注入当前日期、SQLite 跨线程支持
- qdrant-client 新版兼容：`search` 迁移至 `query_points`
- Streamlit：初始化时自动注册 SQLite 表结构、深色主题背景适配

### 验证
- ETL 全流程测试通过
- Qdrant + Ollama + BGE 端到端查询验证通过
