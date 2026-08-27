# API 文档

FastAPI 后端通过应用工厂创建：

```bash
uvicorn module_3_frontend.api:create_app --factory --host 0.0.0.0 --port 8000
```

## 端点

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/query` | 自然语言查询（意图路由 + 混合检索 + LLM 回答） |
| POST | `/api/etl/run` | 触发 ETL 数据接入管线 |
| GET | `/api/schemas` | 获取已注册的数据表 Schema |
| GET | `/api/health` | 健康检查（含组件状态） |
| POST | `/api/export/chart` | 图表导出（PNG / SVG / HTML / Excel，Base64 返回） |

## 认证

配置 `api_key` 后，除 `/api/health`、`/docs`、`/openapi.json`、`/redoc` 外的请求
需携带请求头：

```text
X-API-Key: <your-api-key>
```

## 查询请求示例

```json
{
  "question": "哪个部门的平均销售额最高？",
  "top_k": 5,
  "collection": "default"
}
```
