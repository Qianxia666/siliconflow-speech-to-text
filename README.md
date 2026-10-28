# Speech-To-Text 音频转录校准工具

项目仓库：[Qianxia666/siliconflow-speech-to-text](https://github.com/Qianxia666/siliconflow-speech-to-text)

## 项目简介

一个简单而强大的语音转文字应用，支持多种语音识别 API 接入进行音频转录，并可调用 OpenAI 格式 API 进行校准优化、智能摘要生成和学术笔记制作。

## 功能特点

- 🎤 **音频转录** - 支持上传音频文件进行高精度语音识别
- ✒️ **文本校准优化** - 消除口语化，修正错误，智能分块，支持并发处理
- 🚀 **量子速读** - Map-Reduce架构实现快速摘要生成
- 🎓 **学术笔记生成** - 三阶段处理流程（草稿→审查→优化），专业学术内容整理
- 🔌 **专用模型配置** - 灵活的模型配置体系，支持不同场景优化
- 🔒 **安全配置管理** - 通过环境变量安全管理API密钥
- 📱 **简洁Web界面** - 直观友好的用户交互体验，支持docker部署

## 重大更新
### 20250829 更新
- **新增学术笔记生成功能**：三阶段智能处理，从初稿到最终学术笔记的完整流程
- **专用模型配置优化**：支持针对不同功能的专用模型配置（转录、校准、摘要、笔记）
### 20250609 更新
- 支持通过直接接入AI客户端通过API调用
  1. `s2t-calibrated`提供“音频 -> 校准后文本”服务
  2. `s2t-summarized`提供“音频 -> 最终摘要”服务
- 对后端路由进行了命名空间重构
  1. GET /：Web UI页面服务
  2. POST /api/...：为Web UI服务的内部数据接口
  3. POST /v1/...：为外部开发者服务的API接口
---
### 20250607 更新
- 新增**量子速读**功能：一键生成摘要。支持切换显示 原文/摘要。
### 20250606 更新
- 引入**自动分块**机制: 自动检测长文本，当转录稿超过设定长度时，智能分割成多个语义完整的文本块。避免超出 API Token 限制。
- 引入**上下文感知**机制 : 在处理每个文本块时，将前一文本块的结尾部分作为上下文提示，一并发送给 AI 模型。确保最终文本的连贯性和风格一致性。
- 引入**并发校准**机制: 多线程并发处理，可同时向校准服务 API 发送多个文本块的请求（当前并发数为3）。缩短超长文本的总体处理时间。
- 引入**自动重试**机制: 在单文本块未成功校准时，自动进行最多3次重试。

## 快速开始

### 本地运行

```bash
python -m pip install -r requirements.txt
python app.py
```

服务启动后访问 `http://127.0.0.1:5000/`。至少需要设置 `S2T_API_KEY`；未配置文本优化服务时，转录仍会返回原始文本，摘要和笔记接口会返回配置不可用状态。若通过 `PORT` 修改监听端口，Docker 健康检查会自动使用同一端口。

### 自行构建 Docker

1. **构建镜像**:

    ```bash
    docker build -t speech-to-text .
    ```

2. **部署容器**:

    ```yaml
    version: '3.8'
    services:
      speech-to-text:
        image: speech-to-text:latest
        container_name: speech-to-text
        environment:
          # === 语音转录配置 ===
          # 语音转录 API 地址（可选），默认: https://api.siliconflow.cn/v1/audio/transcriptions
          - S2T_API_URL=https://api.siliconflow.cn/v1/audio/transcriptions
          # 语音转录 API Key（必需）
          - S2T_API_KEY=your-speech-to-text-api-key
          # 语音转录模型（可选），默认: FunAudioLLM/SenseVoiceSmall
          - S2T_MODEL=FunAudioLLM/SenseVoiceSmall
          
          # === 文本优化配置 ===
          # 文本优化 API 地址（可选），默认: https://api.openai.com/v1/chat/completions
          - OPT_API_URL=https://api.openai.com/v1/chat/completions
          # 文本优化 API Key（可选，不配置则跳过校准、摘要、笔记功能）
          - OPT_API_KEY=your-text-optimizing-api-key
          # 默认优化模型（可选）
          - OPT_MODEL=your-default-model
          
          # === 专用模型配置 ===
          # 文本校准专用模型（可选，优先于 OPT_MODEL）
          - CALIBRATION_MODEL=your-calibration-model
          # 摘要生成专用模型（可选，优先于 OPT_MODEL）
          - SUMMARY_MODEL=your-summary-model
          # 笔记生成专用模型（可选，优先于 OPT_MODEL）
          - NOTES_MODEL=your-notes-model
          
          # === API 封装功能配置 ===
          # OpenAI 兼容 API 的认证密钥（可选，启用 API 封装功能时需要）
          - API_ACCESS_TOKEN=your-api-auth-key

          # === 运行参数（可选） ===
          # 服务监听端口，默认 5000
          - PORT=5000
          # 单个上传文件大小上限（MB），默认 100
          - MAX_UPLOAD_MB=100
          # 文本处理字符上限，默认 250000；用于保护模型上下文和服务资源
          - MAX_TEXT_CHARS=250000
          # 上游请求超时时间（秒），默认 300
          - API_TIMEOUT_SECONDS=300
          # 上游失败重试次数与线性退避秒数
          - RETRY_ATTEMPTS=3
          - RETRY_BACKOFF_SECONDS=2
          # 长文本分块和并发参数
          - CHUNK_TARGET_SIZE=5000
          - CHUNK_PROCESSING_THRESHOLD=5500
          - MAX_CONCURRENT_WORKERS=3
          
        ports:
          - "your-port:5000"
    ```

## 技术栈

- 后端：Python Flask
- 前端：HTML, CSS, JavaScript

## 接口

- `GET /healthz`：返回服务运行状态和必要配置是否已加载。
- `POST /api/transcribe`：Web 页面使用的音频转录接口，字段名为 `audio_file`。
- `POST /api/recalibrate`、`POST /api/summarize`、`POST /api/generatenote`：文本处理接口。
- `GET /v1/models`、`POST /v1/audio/transcriptions`：需要 `Authorization: Bearer <API_ACCESS_TOKEN>` 的 OpenAI 兼容接口。

## 验证

```bash
python -m pip install -r requirements-dev.txt
python -m compileall -q app.py tests
python -m unittest discover -v
node --test tests/frontend.test.mjs   # 前端逻辑测试，需要 Node.js 18+
ruff check .
ruff format --check .
```

应用会在 `/healthz` 中报告当前文本和文件上传上限，以及各个上游服务是否已配置。

健康检查 `GET /healthz` 会分别报告转录、校准、摘要和笔记配置状态，并返回当前上传文件大小上限。

## 许可证

MIT License

