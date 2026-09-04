# 首次体验操作手册

本手册把项目的流程收束为一个可重复的闭环：选题 → 内容版本 → 双审批 → 排期 → 小红书草稿。公开发布仍停在最后一个人工确认点。

## 先确认环境

在项目根目录执行：

```bash
uv run alembic upgrade head
uv run uvicorn xhs_manager.api:app --host 127.0.0.1 --port 8000
opencli doctor
opencli xiaohongshu whoami -f json
```

然后打开 `http://127.0.0.1:8000/docs`。每个内部接口都需要 `X-Internal-Token`；令牌只保存在本机 `.env`，不要发到聊天、文档或截图中。

## 体验链路

1. 创建账号和内容任务。
2. 创建研究运行、来源记录、信号和可发布证据包；只把已核验的资料写成事实主张。
3. 创建选题提案，并在 `/v1/approvals/{approval_id}/decisions` 人工批准。
4. 用 `xhs-writer-skill` 生成文案，再用 `guizang-social-card` 生成 3:4 图片；把图片放在项目目录内。
5. 创建内容版本。`asset_paths` 必须是相对于项目根目录的路径，例如 `local-tests/example/output/xhs-01-cover.png`。
6. 人工批准发布审批，创建发布计划。
7. 调用 `POST /v1/publication-plans/{publication_plan_id}/draft`，请求体为：

```json
{"actor_id":"本机操作人标识"}
```

该接口会依次校验：发布审批、任务状态、素材文件、全局暂停、浏览器连接和小红书登录；然后上传草稿，并再次查询草稿箱。相同发布计划重复调用不会重复上传。

## 发布前人工检查

在小红书草稿箱确认：图片顺序、封面、标题、正文、话题和账号。平台对话题的实体匹配并不稳定；如果接口响应带有话题警告，以草稿页中实际选择的结果为准。

确认无误后，由账号持有人在小红书页面点击公开发布。不要在未明确确认的情况下用脚本替代这一步。

## 出错时怎么处理

- `BROWSER_UNAVAILABLE`：运行 `opencli doctor`，确认浏览器扩展仍连接。
- `LOGIN_REQUIRED`：在 Chrome 中重新登录小红书。
- `CONTENT_INCOMPLETE`：补齐标题、正文或项目内图片。
- `PUBLISH_APPROVAL_INVALID`：内容版本或审批已变化，重新走审批和排期。
- `AUTOMATION_PAUSED`：先由有权限的操作人恢复发布自动化。
- 返回 `uncertain`：不要重试上传；先在草稿箱按标题和图片数量核验，避免生成重复草稿。
