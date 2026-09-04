# 本机工具安装与体验清单

本机已完成内容生产、研究、浏览器自动化和视频渲染的基础安装。项目服务使用本地 SQLite，开发配置位于项目根目录 `.env`，真实发布和评论仍保持关闭，直到完成账号授权与发布演练。

## 已安装且已验证

| 能力 | 本机命令或 Skill | 当前状态 | 凭证 |
|---|---|---|---|
| 选题研究 | `agent-reach`、Jina Reader、RSS、GitHub CLI、Exa MCP 配置 | 命令可用；公开网页与 RSS 已验证 | GitHub 公开研究无需 Key；私人仓库需 `gh auth login` |
| 小红书浏览器自动化 | `opencli`、`opencli-browser` Skill | 守护进程、Chrome 扩展和小红书登录均已验证 | 依赖账号持有人手动登录；不需要开发者 API Key |
| 图文初稿 | `xhs-writer-skill` | 已安装到 Codex Skill 目录 | 纯文字卡片无需 Key；图生图需要 OpenAI API Key |
| 卡片排版 | `guizang-social-card`、`baoyu-xhs-images`、`baoyu-diagram` | 已安装 | Playwright/本机浏览器；可选图片服务 Key |
| 文案编辑 | `humanizer-zh` | 已安装 | 无需 Key |
| 视频 | `hyperframes`、`hyperframes-core`、`hyperframes-cli`、`general-video`、`media-use` | `hyperframes doctor` 已验证 Node、Chrome、FFmpeg | 本地渲染无需 Key；云端渲染需 HeyGen/AWS 授权 |

可选但尚未配置的工具不进入图文主链路：ChatCut（账户 OAuth 与积分）、Flova AI（账户与积分）、魔因漫创（桌面应用与模型供应商 Key）。

## 当前可体验流程

1. 启动服务：`uv run alembic upgrade head && uv run uvicorn xhs_manager.api:app --host 127.0.0.1 --port 8000`。
2. 打开 `http://127.0.0.1:8000/docs`，使用 `.env` 中的 `XHS_INTERNAL_API_TOKEN` 调用内部接口。
3. 用研究接口创建研究运行、来源执行、标准化信号和证据包；搜索摘要不能作为已核验关键事实。
4. 调用 `/v1/topic-proposals` 创建选题；只有可用证据包才能进入选题审批。批准后任务进入制作状态。
5. 在 Codex 新一轮对话中使用已安装 Skill 生成初稿和卡片，例如：`用 xhs-writer-skill 为“AI 工作流实验员”写一篇关于……的小红书图文，输出到本项目 data/assets/`。
6. 用 `guizang-social-card` 生成并检查 3:4 卡片；用 `humanizer-zh` 只做事实检查后的二次编辑；再用 `/v1/content-versions` 归档内容并生成发布审批。
7. 发布审批通过且排期有效后，用 `/v1/publication-plans` 创建发布计划，再调用 `POST /v1/publication-plans/{publication_plan_id}/draft` 上传到小红书草稿箱。该接口会预检账号、暂停状态、审批、版本和素材，并将平台草稿标识写进幂等外部动作记录；最终公开发布前必须由账号持有人在页面确认。

## 必须由账号持有人完成的授权

浏览器扩展和小红书登录受 Chrome 与平台安全机制保护，不能由后台脚本替代。完成下列步骤后运行 `opencli doctor`：

1. 在 Chrome 安装并启用 [OpenCLI Browser Bridge](https://chromewebstore.google.com/detail/opencli/ildkmabpimmkaediidaifkhjpohdnifk)。
2. 保持 Chrome 打开，在独立的运营 Profile 中手动登录小红书；不要复用日常浏览器 Profile。
3. 在终端运行 `opencli doctor`，预期看到 Extension 与 Connectivity 均为 `OK`。
4. 如需私有 GitHub 素材，在终端运行 `gh auth login` 并按官方交互流程授权。
5. 如需图生图，把 OpenAI Key 放到个人密钥环境或对应 Skill 的私有 `.env`；不得写入本项目数据库、文档、日志或审批正文。

完成第 1 至 3 步后，研究和“准备发布页”的浏览器演练可以继续；最终点击发布仍必须经过项目的发布审批和一次人工确认。

## 首次端到端演练记录（2026-07-30）

演练主题为“别把聊天记录当工作流”。产物位于 `local-tests/xhs-ai-workflow-experience/`：6 张 `1080×1440` 图文卡片、`post.md` 正文和 `post.json` 发布元数据。

- 账号登录检查：`opencli xiaohongshu whoami -f json` 成功。
- 上传结果：已创建一条含 6 张图片、标题为“别把聊天记录当工作流”的小红书草稿。
- 标签限制：平台自动选择 `提示词工程` 时没有出现可确认的话题实体，命令返回失败，但草稿已生成。不要把“话题字符串”当作“平台已绑定话题”；下一次应先仅使用搜索确认过的标签，或在草稿页手动选择。
- 未执行公开发布。本项目的默认安全边界是：先保存草稿，再由账号持有人明确确认最终发布。

## 本机诊断

```bash
agent-reach doctor --json
opencli doctor
npx --yes hyperframes doctor
uv run pytest
```

## 已知限制

- 项目已将选题版本、内容版本和发布计划接入审批状态机；真实研究连接器和小红书发布执行器仍待接入。
- OpenCLI 当前已连接并可创建小红书草稿；某些话题名未必能被平台解析为可选话题实体，需在发布前回查。
- HyperFrames 的可选本地转写、TTS、BGM 和 Docker 检查项未安装，不影响静态图文或基础本地视频渲染。
