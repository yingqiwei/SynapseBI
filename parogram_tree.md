项目一：SynapseBI —— 企业级多模态知识库与智能决策中枢
	• 项目名称：SynapseBI（意为连接数据与业务的神经突触）
	• 项目场景：打通企业内部异构数据（PDF报告、Excel表格、SQL数据库），实现自然语言的精准问答与BI图表生成。
🛠️ 落地方案
	1. 数据接入与预处理管线 (ETL)：
		○ 编写 Python 脚本，利用 OCR 与版面分析工具（如 PaddleOCR 或 Unstructured）解析带表格/图表的复杂 PDF。
		○ 使用 Pandas 自动清洗、规范化 Excel 数据，并提取元数据（Metadata）与结构化 Schema。
		
	2. 混合检索引擎设计：
		○ 非结构化数据：切片后存入向量数据库（如 Milvus 或 Qdrant），结合 BGE 向量模型。
		○ 结构化数据：通过 Text-to-SQL 技术，将自然语言转换为安全的 SQL 查询，直接读取企业关系型数据库。
		○ 路由层：设计一个轻量级 Router（意图识别），判断用户问题应流向向量检索还是 SQL 查询。
	3. 前端交互与安全隔离：
		○ 使用 Streamlit 或 React 构建简洁的业务端看板，支持一键导出图表。
采用 Docker 容器化部署，支持本地私有化大模型（如 Qwen 或 Llama 3）以满足企业数据隐私要求。