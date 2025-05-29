# SiliconFlow Speech-to-Text

一个简单而强大的语音转文字 Web 应用，支持多种语音识别 API 接入。

## 功能特点

- 🎤 支持上传音频文件进行转写
- 🔌 灵活的 API 接入，支持多种语音识别服务（如 siliconflow 等）
- 🌐 简洁的 Web 界面
- 🔒 安全的 API 密钥管理

## 技术栈

- 后端：Python Flask
- 前端：HTML, CSS, JavaScript
- API：支持任何符合接口规范的语音识别 API

## 快速开始

### 环境要求

- Python 3.x
- pip（Python 包管理器）

### 安装步骤

1. 克隆仓库
```bash
git clone https://github.com/Qianxia666/siliconflow-speech-to-text
cd siliconflow-speech-to-text
```

2. 安装依赖
```bash
pip install -r requirements.txt
```

3. 运行应用
```bash
python app.py
```

4. 在浏览器中访问 `http://localhost:5000`

## Deploy to Vercel

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https%3A%2F%2Fgithub.com%2FQianxia666%2Fsiliconflow-speech-to-text)

## 使用说明

1. 打开应用后，你会看到一个简洁的上传界面
2. 准备以下信息：
   - 音频文件（支持常见音频格式）
   - API 端点 URL
   - API 密钥
   - 模型名称
3. 上传音频文件并填写相关信息
4. 点击转写按钮，等待结果
5. 转写完成后，文字结果会显示在页面上

## API 接入说明

本应用支持接入任何符合以下接口规范的语音识别 API：

- 请求方式：POST
- 请求参数：
  - `file`：音频文件
  - `model`：模型名称
- 认证方式：Bearer Token
- 返回格式：JSON，包含 `text` 字段

## 贡献指南

欢迎提交 Issue 和 Pull Request！

## 许可证

MIT License

## 联系方式

如有问题或建议，欢迎通过以下方式联系：
- 提交 Issue

## 致谢

感谢所有为本项目做出贡献的开发者！ 