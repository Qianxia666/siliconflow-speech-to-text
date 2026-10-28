import hmac
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests
from flask import Flask, Response, jsonify, render_template, request
from waitress import serve

app = Flask(__name__, static_folder="static", static_url_path="/static")

REPOSITORY_URL = "https://github.com/Qianxia666/siliconflow-speech-to-text"


def _positive_int_env(name, default, minimum=1, maximum=None):
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        print(f"警告: 环境变量 {name} 必须是整数，已使用默认值 {default}。")
        return default
    if value < minimum or (maximum is not None and value > maximum):
        print(f"警告: 环境变量 {name} 超出允许范围，已使用默认值 {default}。")
        return default
    return value


def _text_env(name, default=None):
    """Read a text environment variable without treating whitespace as a value."""
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    value = raw_value.strip()
    return value or None


# --- 服务配置 ---
S2T_API_URL = _text_env(
    "S2T_API_URL", "https://api.siliconflow.cn/v1/audio/transcriptions"
)
S2T_API_KEY = _text_env("S2T_API_KEY")
S2T_MODEL = _text_env("S2T_MODEL", "FunAudioLLM/SenseVoiceSmall")

OPT_API_URL = _text_env("OPT_API_URL", "https://api.openai.com/v1/chat/completions")
OPT_API_KEY = _text_env("OPT_API_KEY")
OPT_MODEL = _text_env("OPT_MODEL")  # 默认模型

# --- 各功能专用模型配置（向后兼容）---
CALIBRATION_MODEL = _text_env("CALIBRATION_MODEL") or OPT_MODEL  # 校准模型
SUMMARY_MODEL = _text_env("SUMMARY_MODEL") or OPT_MODEL  # 摘要(量子速读)模型
NOTES_MODEL = _text_env("NOTES_MODEL") or OPT_MODEL  # 笔记生成模型

# --- 分块处理配置 ---
CHUNK_TARGET_SIZE = _positive_int_env("CHUNK_TARGET_SIZE", 5000, 500, 50000)
CHUNK_PROCESSING_THRESHOLD = max(
    CHUNK_TARGET_SIZE,
    _positive_int_env("CHUNK_PROCESSING_THRESHOLD", 5500, 500, 50000),
)
MAX_CONCURRENT_WORKERS = _positive_int_env("MAX_CONCURRENT_WORKERS", 3, 1, 16)
RETRY_ATTEMPTS = _positive_int_env("RETRY_ATTEMPTS", 3, 1, 10)
API_TIMEOUT_SECONDS = _positive_int_env("API_TIMEOUT_SECONDS", 300, 5, 1800)
RETRY_BACKOFF_SECONDS = _positive_int_env("RETRY_BACKOFF_SECONDS", 2, 1, 60)
MAX_UPLOAD_MB = _positive_int_env("MAX_UPLOAD_MB", 100, 1, 1024)
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_TEXT_CHARS = _positive_int_env("MAX_TEXT_CHARS", 250000, 1000, 5000000)
MULTIPART_OVERHEAD_BYTES = 1024 * 1024
SERVER_PORT = _positive_int_env("PORT", 5000, 1, 65535)

app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES

# --- OpenAI 兼容 API 配置 ---
API_ACCESS_TOKEN = _text_env("API_ACCESS_TOKEN")
MODEL_CALIBRATE = "s2t-calibrated"
MODEL_SUMMARIZE = "s2t-summarized"
MODEL_CREATED_AT = int(time.time())

RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


class ServiceError(Exception):
    """An expected upstream/configuration error with an HTTP status for callers."""

    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


# --- Prompts ---
HARDCODED_OPTIMIZATION_PROMPT = """
Description:
你是一位录音文字校准专家，能够消除口语表达中的停顿、重复和口语化语气词等常见问题，同时能解决录音软件在记录文字时会产生的错别字、多音字记录不准等技术问题，将口语化的录音文件转换为书面文字。
Background:
在录音转写过程中，录音软件经常会因为口音、语速等原因产生一些错别字、多音字记录不准等问题。
此外，口语表达中的停顿、重复和口语化语气词等也会影响文字的流畅和准确性。
为了将录音文件转写为高质量的书面文字，需要对这些问题进行校准和修正。
Goals:
1.消除录音文字中的停顿、重复和口语化语气词。
2.修正录音文字中的错别字和多音字。
3.确保转写文字保持原文完整性，不遗失任何细节。
4.提供高质量、流畅的书面文字版本。
Constraints:
1.保持原文的完整性，不得删除或遗漏任何信息。
2.仅校准，而不改写任何原文，确保文字内容忠实于原录音。
3.修正错误时需保证语义的准确性和连贯性。
4.校准过程中不得改变原文意思。
5.绝对不会对原文做概括和缩略性的总结，仅仅做出校对。
Skills:
熟悉录音转写的常见问题及其解决方法。
精通中文，能够识别并修正错别字和多音字。
具有良好的语言表达能力，能够将口语化的内容转换为流畅的书面文字。
具备细致入微的校对能力，确保文字的准确性和完整性。
Workflows:
输入: 根据用户提交的录音转化为文字稿。
校准: 消除停顿、重复和口语化语气词，修正错别字和多音字。
输出: 提供校准后的高质量书面文字版本。
检查: 确认修正后的文字保持原文完整性和准确性。
"""

PROMPT_SUMMARY_MAP = """
Description:
你是一位信息分析专家，正在执行一个大型文档分析任务的第一步。你的当前任务是，从提供给你的【文档片段】中，高效、精准地提取出所有的核心信息和关键要点。你提取出的要点将作为后续最终摘要整合的【原材料】。
Skills：
1.提炼关键信息：快速识别并提取关键词、主题句或中心思想。
2.逻辑梳理：理解文字的内在逻辑，确保提取的要点条理清晰。
3.分类归类：能够将片段内的相关信息点进行有效归纳。
4.概括提升：在归纳的基础上，提炼出更高层次的概念或结论。
5.精简语言：使用最凝练的语言来表达要点，去除所有不必要的修饰和口语化内容。
Rules：
1.准确全面：提取的要点必须准确反映【文档片段】的核心内容，不能遗漏重要信息。
2.客观中立：仅提取事实和观点，不掺杂个人情绪或进行二次解读。
3.重点突出：把握住片段的重点和主旨，突出核心内容。
Workflows：
1. 仔细阅读用户给出的【文档片段】原文。
2. 根据 <Rules> 对原文进行分析，提取出所有关键信息点。
3. 将这些关键信息点整理成一个无序列表（bullet points）进行输出。
Output Format:
- [要点1]
- [要点2]
- [要点3]
Constraints:
1. 忠于原文，不得改变【文档片段】的原意。
2. 只输出要点列表，不要输出任何额外的标题、开头、结尾、解释或说明。例如，不要写“以下是本文档片段的要点：”。
3. 每个要点都应是独立的、有意义的信息单元。
"""

PROMPT_SUMMARY_REDUCE = """
Description:
你是一位资深的首席编辑，你的任务是将一份由多位助理从一篇长篇录音稿中分段提取的【核心要点清单】进行最终整合，撰写成一份完整、流畅、逻辑清晰的最终摘要，供高层决策者阅读。
Background:
你收到的【核心要点清单】是按原文顺序排列的，但因为是分段提取，所以可能存在轻微的重复、风格不一或上下文断裂的问题。你的专业价值在于消除这些问题，创造出一篇浑然一体的最终作品。
Skills：
1. 全局视野：能够从零散的要点中洞察整个文档的宏观结构和核心主旨。
2. 叙事构建：擅长将独立的要点有机地串联起来，构建出有开头、有主体、有结尾的流畅叙事。
3. 逻辑重构：识别并梳理要点之间的逻辑关系（因果、并列、递进等），形成严密的逻辑链条。
4. 语言润色：使用专业、精炼、书面化的语言，统一全文风格，提升文本的专业性和可读性。
5. 综合概括：在整合所有要点的基础上，进行更高层次的归纳与升华。
Rules：
1. 全面覆盖：最终摘要必须全面反映【核心要点清单】中提供的所有关键信息，不得遗漏。
2. 连贯流畅：摘要的段落和句子之间必须过渡自然，逻辑通顺。
3. 结构清晰：摘要应有引言部分概括主旨，主体部分分点或分段阐述，结论部分进行总结。
4. 客观准确：忠实于要点清单的内容，不添加清单之外的信息或个人主观臆断。
Workflows：
1. 通读并理解整个【核心要点清单】。
2. 在脑海中构思出最终摘要的宏观结构。
3. 开始撰写，将独立的要点无缝地融入到流畅的段落中。
4. 撰写完成后，进行通篇审阅，检查逻辑、流畅性和完整性。
5. 输出最终的、可以直接发布的摘要成品，不要输出任何额外的解释或说明。
Constraints:
1. 绝对不要在摘要中提及“根据提供的要点”、“此清单显示”或“本文档的要点是”这类元语言。你必须让读者感觉，这份摘要是你直接阅读了全文后撰写的。
2. 不要对要点进行简单的罗列或堆砌。你的核心任务是【整合】与【重构】。
3. 输出格式应为一篇格式化良好、适合人类阅读的完整文章。
"""

PROMPT_NOTES_REDUCE = """
你是一名学术笔记总编。请把输入的多份分段笔记整合为一份完整、去重、逻辑严谨的中文学术笔记。
必须保留所有关键事实、论点、数据、案例、公式和结论，不添加输入之外的信息。
统一术语和结构，消除重复内容与分段造成的上下文断裂。
输出使用层级清晰的 Markdown：以一个一级标题开始，编号主题使用二级标题，必要时使用列表和表格。
只输出最终笔记，不要解释整合过程，也不要使用代码块包裹。
"""

PROMPT_GENERATE_NOTES = """
我是一名世界顶尖学术机构专精于定性数据分析的高级研究助理，能够将用户提供的非结构化文本（如视频转录稿等）转化为结构清晰、信息密集的学术笔记。我的分析过程严谨、确定，推理温度设定在0.2，以确保最高水平的学术准确性和逻辑严密性。

我处理的所有文本都必须被包裹在`<待处理文本>`标签内。

我的核心任务是深度提炼、整合并构建信息，最终生成一份逻辑严谨、可直接用于学术报告或研究论文的高质量中文笔记。为此，我遵循一个严格的三阶段自我校对工作流程：

**第一阶段：草稿生成**
我会根据以下步骤，创建一份初步的学术笔记草稿：
1. **核心论点提炼**：首先，用一句话精准概括所提供文本的核心论点或研究问题，确立整篇笔记的主线。
2. **深度分析与主题识别**：通读并分析全部内容，识别出贯穿全文的核心主题、主要论点和基本逻辑框架，剔除所有广告、口头禅、无关闲聊等干扰信息。
3. **结构化信息重组**：在理解整体结构后，将所有相关信息进行逻辑分组和层级归类。主动识别并整理内容中的核心概念、关键论据、支撑数据、案例分析、数学公式和重要结论，将它们置于最合适的逻辑层级之下。
4. **精炼总结与量化提炼**：在结构化基础上，对每个信息组进行综合性提炼与总结。每个子主题下的核心要点总结控制在50字以内，以最精确、简洁的学术语言概括其实质内容。

**第二阶段：自我批判与审查**
生成草稿后，我会暂停并启动内部审查机制。从批判性学者的角度，全面审视草稿的：
- **逻辑连贯性**：检查论证链条的完整性和推理的严密性
- **信息密度**：确保每个段落都具有高信息价值，无冗余表达
- **结构清晰度**：验证层次分明，逻辑流畅
- **学术规范性**：检查语言风格、术语使用和格式要求的一致性

识别出任何潜在的逻辑漏洞、总结不足或结构不清之处。

**第三阶段：最终版本输出**
基于自我审查结果，重写并优化草稿，生成最终的精炼版本。

我的最终输出必须严格遵守以下规范：

**格式要求：**
- 所有输出采用层级分明的Markdown格式，使用标准标题层次（#, ##, ###）、嵌套列表（-）和粗体（**）构建清晰的视觉和逻辑结构
- 所有编号主题统一使用H2格式：`## 1. 主题名称`，避免渲染错误
- 数学公式必须使用标准LaTeX语法，确保Markdown环境正确渲染
- 布局优化,通过项目符号、表格和简洁段落增强可读性
-
**语调与风格：**
- 始终保持专业、客观、正式的学术语调
- 语言力求精确、简洁、无歧义，避免口语化或主观性表达
- 专有名词、技术术语、品牌名称、人名保留英文原文以确保准确性

**内容焦点：**
- 聚焦信息实质内容，突出核心概念、关键论据、支撑数据和最终结论
- 确保笔记具有极高的信息密度和学术价值
- 保留所有重要的计算过程、推导步骤和量化分析

**交付物：**
- 输出必须直接以一级Markdown标题（#）开始，严格遵循所构建的笔记结构
- 纯Markdown文本输出，不使用任何代码块包裹

现在，请在`<待处理文本>`标签内提供您的视频转录内容，我将严格按照以上三阶段工作流程为您生成高质量的学术笔记。
"""


# --- 辅助函数 ---
def _extract_api_error_message(response):
    """Extract a short error message from JSON or text upstream responses."""
    try:
        error_detail = response.json()
    except (ValueError, TypeError):
        return (getattr(response, "text", "") or "上游服务未提供错误详情")[:200]

    if isinstance(error_detail, dict):
        error_value = error_detail.get("error")
        if isinstance(error_value, dict):
            api_err_msg = (
                error_value.get("message")
                or error_value.get("detail")
                or error_value.get("code")
            )
        else:
            api_err_msg = error_value
        api_err_msg = (
            api_err_msg or error_detail.get("message") or error_detail.get("detail")
        )
        if api_err_msg:
            return str(api_err_msg)[:200]
    return (getattr(response, "text", "") or "上游服务未提供错误详情")[:200]


def _is_valid_http_url(value):
    if not isinstance(value, str) or not value.strip():
        return False
    candidate = value.strip()
    if any(char.isspace() for char in candidate):
        return False
    try:
        parsed = urlparse(candidate)
        hostname = parsed.hostname
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(hostname)


def _opt_configuration_error(model, feature_name):
    missing = []
    if not OPT_API_KEY:
        missing.append("缺少API Key")
    if not _is_valid_http_url(OPT_API_URL):
        missing.append("API URL无效")
    if not model:
        missing.append(f"缺少{feature_name}模型名称")
    if not missing:
        return None
    return f"{feature_name}服务配置不完整: {', '.join(missing)}"


def _extract_chat_content(data):
    """Read string or content-part responses from OpenAI-compatible APIs."""
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return ""
    message = first_choice.get("message") or first_choice.get("delta") or {}
    content = message.get("content") if isinstance(message, dict) else ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts).strip()
    return ""


def _uploaded_file_size(audio_file):
    """Return the uploaded stream size without leaving a seekable stream at EOF."""
    stream = getattr(audio_file, "stream", None)
    if stream is None:
        return None

    current_position = None
    moved = False
    try:
        current_position = stream.tell()
        stream.seek(0, os.SEEK_END)
        moved = True
        size = stream.tell()
        stream.seek(current_position)
        moved = False
        return size
    except (AttributeError, OSError, ValueError):
        # A partially seekable stream can fail while restoring its original
        # position. Never knowingly leave it at EOF: uploads typically begin
        # at position 0, which is a safer recovery point when restoration fails.
        if moved:
            recovery_positions = []
            if current_position is not None:
                recovery_positions.append(current_position)
            if current_position != 0:
                recovery_positions.append(0)
            for position in recovery_positions:
                try:
                    stream.seek(position)
                    break
                except (AttributeError, OSError, ValueError):
                    continue
        return None


def _validate_text(value, description):
    """Validate user-provided text without changing its meaningful whitespace."""
    if not isinstance(value, str) or not value.strip():
        raise ServiceError(f"{description}不能为空", 400)
    if len(value) > MAX_TEXT_CHARS:
        raise ServiceError(f"{description}过长，最大支持 {MAX_TEXT_CHARS} 个字符", 413)
    return value


def _retry_delay(attempt):
    """Return the linear retry delay after a zero-based failed attempt."""
    return RETRY_BACKOFF_SECONDS * (attempt + 1)


def _request_chat_completion(messages, model, temperature, operation):
    """Call an OpenAI-compatible endpoint with bounded retries and safe parsing."""
    payload = {"model": model, "messages": messages, "temperature": temperature}
    headers = {
        "Authorization": f"Bearer {OPT_API_KEY}",
        "Content-Type": "application/json",
    }
    error_message = "未知错误"

    for attempt in range(RETRY_ATTEMPTS):
        try:
            response = requests.post(
                OPT_API_URL,
                headers=headers,
                json=payload,
                timeout=API_TIMEOUT_SECONDS,
            )
            if 200 <= response.status_code < 300:
                try:
                    content = _extract_chat_content(response.json())
                except (ValueError, TypeError):
                    content = ""
                if content:
                    return {"status": "success", "content": content}
                error_message = f"{operation}API返回空内容或格式无效"
            else:
                error_message = f"API错误 {response.status_code}: {_extract_api_error_message(response)}"
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    return {"status": "error", "message": error_message}
        except requests.exceptions.Timeout:
            error_message = "请求超时"
        except requests.exceptions.RequestException as exc:
            error_message = f"网络连接错误: {type(exc).__name__}"
        except Exception as exc:
            error_message = f"未知错误: {type(exc).__name__}"

        if attempt == RETRY_ATTEMPTS - 1:
            return {"status": "error", "message": error_message}
        time.sleep(_retry_delay(attempt))

    return {"status": "error", "message": error_message}


def _request_text_transformation(text, system_prompt, model, temperature, operation):
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ]
    return _request_chat_completion(messages, model, temperature, operation)


def _map_with_bounded_workers(function, items):
    """Map work in-order while avoiding unnecessary thread pools for tiny jobs."""
    items = list(items)
    if not items:
        return []
    if len(items) == 1:
        return [function(items[0])]
    worker_count = min(MAX_CONCURRENT_WORKERS, len(items))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        return list(executor.map(function, items))


def _reduce_text_fragments(
    fragments, system_prompt, model, temperature, operation, max_levels=8
):
    """Hierarchically reduce fragments so no user message exceeds the chunk target."""
    pending = [
        fragment
        for fragment in fragments
        if isinstance(fragment, str) and fragment.strip()
    ]
    if not pending:
        return {"status": "error", "message": f"{operation}没有可处理的内容"}

    for level in range(1, max_levels + 1):
        combined = "\n\n".join(pending)
        batches = [
            batch
            for batch in _split_text_intelligently(
                combined, chunk_size=CHUNK_TARGET_SIZE
            )
            if batch.strip()
        ]

        def reduce_batch(batch):
            return _request_text_transformation(
                batch,
                system_prompt,
                model,
                temperature,
                f"{operation}第{level}层",
            )

        results = _map_with_bounded_workers(reduce_batch, batches)
        failed = [result for result in results if result["status"] != "success"]
        if failed:
            return failed[0]

        pending = [result["content"] for result in results]
        if len(pending) == 1:
            return {"status": "success", "content": pending[0]}

    return {
        "status": "error",
        "message": f"{operation}在 {max_levels} 层归并后仍未收敛",
    }


# 智能分块策略函数
def _split_text_intelligently(text, chunk_size=CHUNK_TARGET_SIZE):
    """Split text without losing characters, preferring semantic boundaries."""
    if (
        isinstance(chunk_size, bool)
        or not isinstance(chunk_size, int)
        or chunk_size < 1
    ):
        raise ValueError("chunk_size 必须是正整数")
    if not isinstance(text, str) or not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    delimiters = ["。", "！", "？", "；", ";", "!", "?", "\n"]
    chunks = []
    start_index = 0

    while start_index < len(text):
        end_index = min(start_index + chunk_size, len(text))
        if end_index >= len(text):
            chunks.append(text[start_index:])
            break

        best_split_pos = max(
            (
                text.rfind(delimiter, start_index + 1, end_index)
                for delimiter in delimiters
            ),
            default=-1,
        )

        if best_split_pos > start_index:
            split_index = best_split_pos + 1
        else:
            # For unpunctuated/English text, prefer the last whitespace boundary.
            whitespace_pos = max(
                (text.rfind(char, start_index + 1, end_index) for char in (" ", "\t")),
                default=-1,
            )
            split_index = (
                whitespace_pos + 1 if whitespace_pos > start_index else end_index
            )

        chunks.append(text[start_index:split_index])
        start_index = split_index

    # Do not filter whitespace-only chunks: dropping one would mutate the source text.
    return chunks


def _get_last_sentence(text):
    if not isinstance(text, str) or not text.strip():
        return ""
    sentences = [
        part.strip()
        for part in re.split(r"(?<=[。？！；;!?\n])", text.strip())
        if part.strip()
    ]
    return sentences[-1] if sentences else text.strip()


def _optimize_chunk_with_retry(chunk_data):
    text_chunk = chunk_data["text"]
    # Preserve separator-only chunks exactly instead of sending meaningless
    # whitespace to an upstream model that may reject or erase it.
    if not text_chunk.strip():
        return {"status": "success", "content": text_chunk}
    context_sentence = chunk_data.get("context")
    messages = [{"role": "system", "content": HARDCODED_OPTIMIZATION_PROMPT}]
    if context_sentence:
        user_content = (
            f"为了保持上下文连贯，这是紧接在当前文本之前的最后一句话：\n---CONTEXT---\n{context_sentence}\n---END CONTEXT---\n\n"
            f"请仅校准并返回以下这段新的文本，不要在你的回答中重复上面的上下文内容：\n---TEXT TO CALIBRATE---\n{text_chunk}\n---END TEXT---"
        )
    else:
        user_content = text_chunk
    messages.append({"role": "user", "content": user_content})
    return _request_chat_completion(messages, CALIBRATION_MODEL, 0.1, "校准")


def _perform_text_optimization(raw_text_to_optimize):
    if not isinstance(raw_text_to_optimize, str) or not raw_text_to_optimize.strip():
        return "", "校准失败 (待校准文本为空)", False
    if len(raw_text_to_optimize) > MAX_TEXT_CHARS:
        return (
            raw_text_to_optimize,
            f"校准已跳过 (文本超过 {MAX_TEXT_CHARS} 个字符)",
            False,
        )

    config_error = _opt_configuration_error(CALIBRATION_MODEL, "校准")
    if config_error:
        opt_status_message = f"校准已跳过 ({config_error})"
        print(f"OPT API 配置不完整，跳过文本优化。原因: {opt_status_message}")
        return raw_text_to_optimize, opt_status_message, False

    if len(raw_text_to_optimize) <= CHUNK_PROCESSING_THRESHOLD:
        print("文本较短，直接进行单次校准...")
        result = _optimize_chunk_with_retry({"text": raw_text_to_optimize})
        if result["status"] == "success":
            return result["content"], "校准成功！", True
        else:
            return raw_text_to_optimize, f"校准失败 ({result['message']})", False

    print(f"文本过长({len(raw_text_to_optimize)}字)，启动分块并发校准...")
    chunks = _split_text_intelligently(
        raw_text_to_optimize, chunk_size=CHUNK_TARGET_SIZE
    )
    tasks = []
    previous_context = None
    for chunk in chunks:
        tasks.append({"text": chunk, "context": previous_context})
        if chunk.strip():
            previous_context = _get_last_sentence(chunk) or previous_context
    processed_results = _map_with_bounded_workers(_optimize_chunk_with_retry, tasks)
    failed_chunks = [res for res in processed_results if res["status"] == "error"]
    if failed_chunks:
        first_error_message = failed_chunks[0]["message"]
        print(
            f"校准过程中有块处理失败，回退到原始文本。失败原因: {first_error_message}"
        )
        return raw_text_to_optimize, f"校准失败 ({first_error_message})", False
    else:
        full_optimized_text = "".join([res["content"] for res in processed_results])
        print("所有块均已成功校准并合并。")
        return full_optimized_text, "校准成功！", True


# 要点提取逻辑函数
def _summarize_chunk_with_retry(text_chunk):
    """
    使用PROMPT_SUMMARY_MAP prompt
    对每个文本块提取关键信息点
    输出格式：无序列表(bullet points)
    重试机制：失败时重试最多3次
    """
    return _request_text_transformation(
        text_chunk, PROMPT_SUMMARY_MAP, SUMMARY_MODEL, 0.1, "摘要Map阶段"
    )


# 后端核心处理流程
def _perform_summarization(text_to_summarize):
    if not isinstance(text_to_summarize, str) or not text_to_summarize.strip():
        return {"status": "error", "message": "待总结文本不能为空", "status_code": 400}
    if len(text_to_summarize) > MAX_TEXT_CHARS:
        return {
            "status": "error",
            "message": f"待总结文本过长，最大支持 {MAX_TEXT_CHARS} 个字符",
            "status_code": 413,
        }
    config_error = _opt_configuration_error(SUMMARY_MODEL, "摘要")
    if config_error:
        return {"status": "error", "message": config_error, "status_code": 503}

    print("开始总结任务: Map 阶段 - 并发提取要点...")
    # Step 1: 智能文本分块（_split_text_intelligently()）
    chunks = [
        chunk
        for chunk in _split_text_intelligently(
            text_to_summarize, chunk_size=CHUNK_TARGET_SIZE
        )
        if chunk.strip()
    ]
    if not chunks:
        return {
            "status": "error",
            "message": "待总结文本为空或分割失败",
            "status_code": 400,
        }
    # Step 2: Map阶段 - 并发要点提取（_summarize_chunk_with_retry()），按CHUNK_TARGET_SIZE（默认5000）字符分块，并发处理每个文本块
    map_results = _map_with_bounded_workers(_summarize_chunk_with_retry, chunks)
    failed_chunks = [res for res in map_results if res["status"] == "error"]
    if failed_chunks:
        first_error = failed_chunks[0]["message"]
        return {
            "status": "error",
            "message": f"提取要点失败 ({first_error})",
            "status_code": 502,
        }
    print("Map 阶段成功。开始 Reduce 阶段 - 整合生成最终摘要...")
    # Step 3: Reduce阶段 - 分层整合最终摘要，避免归并内容再次超过上下文限制
    reduce_result = _reduce_text_fragments(
        [res["content"] for res in map_results],
        PROMPT_SUMMARY_REDUCE,
        SUMMARY_MODEL,
        0.2,
        "摘要Reduce阶段",
    )
    if reduce_result["status"] == "success":
        return {"status": "success", "summary": reduce_result["content"]}
    return {
        "status": "error",
        "message": f"整合摘要失败 ({reduce_result['message']})",
        "status_code": 502,
    }


# 笔记生成核心处理函数
def _perform_notes_generation(text_to_process):
    """
    生成笔记功能
    - 对短文本直接生成，对长文本进行分块并发处理和分层归并
    - 将每个原文块包裹在<待处理文本>标签内
    - 使用PROMPT_GENERATE_NOTES作为系统prompt
    - 设置temperature=0.2确保学术准确性
    - 包含重试机制
    """
    if not isinstance(text_to_process, str) or not text_to_process.strip():
        return {"status": "error", "message": "待处理文本不能为空", "status_code": 400}
    if len(text_to_process) > MAX_TEXT_CHARS:
        return {
            "status": "error",
            "message": f"待处理文本过长，最大支持 {MAX_TEXT_CHARS} 个字符",
            "status_code": 413,
        }

    config_error = _opt_configuration_error(NOTES_MODEL, "笔记生成")
    if config_error:
        return {"status": "error", "message": config_error, "status_code": 503}

    print(f"开始生成笔记，文本长度: {len(text_to_process)} 字符")

    chunks = [
        chunk
        for chunk in _split_text_intelligently(
            text_to_process, chunk_size=CHUNK_TARGET_SIZE
        )
        if chunk.strip()
    ]

    def generate_chunk(text_chunk):
        wrapped_text = f"<待处理文本>\n{text_chunk.strip()}\n</待处理文本>"
        return _request_text_transformation(
            wrapped_text, PROMPT_GENERATE_NOTES, NOTES_MODEL, 0.2, "笔记生成"
        )

    map_results = _map_with_bounded_workers(generate_chunk, chunks)
    failed_chunks = [result for result in map_results if result["status"] != "success"]
    if failed_chunks:
        return {**failed_chunks[0], "status_code": 502}

    if len(map_results) == 1:
        result = map_results[0]
    else:
        result = _reduce_text_fragments(
            [item["content"] for item in map_results],
            PROMPT_NOTES_REDUCE,
            NOTES_MODEL,
            0.2,
            "笔记归并",
        )
    if result["status"] == "success":
        return {"status": "success", "notes": result["content"]}
    return {**result, "status_code": 502}


def _transcribe_audio_file(audio_file):
    if not S2T_API_KEY:
        raise ServiceError("语音转录服务未配置 S2T_API_KEY", 503)
    if not _is_valid_http_url(S2T_API_URL):
        raise ServiceError("语音转录服务的 S2T_API_URL 无效", 503)
    if not S2T_MODEL:
        raise ServiceError("语音转录服务未配置模型名称", 503)
    if (
        not audio_file
        or not isinstance(audio_file.filename, str)
        or not audio_file.filename.strip()
    ):
        raise ServiceError("上传的音频文件名不能为空", 400)
    file_size = _uploaded_file_size(audio_file)
    if file_size == 0:
        raise ServiceError("上传的音频文件不能为空", 400)
    if file_size is not None and file_size > MAX_UPLOAD_BYTES:
        raise ServiceError(f"上传文件过大，最大支持 {MAX_UPLOAD_MB} MB", 413)

    files = {
        "file": (
            audio_file.filename,
            audio_file.stream,
            audio_file.mimetype or "application/octet-stream",
        )
    }
    payload = {"model": S2T_MODEL}
    headers = {"Authorization": f"Bearer {S2T_API_KEY}"}

    stream = audio_file.stream
    try:
        initial_position = stream.tell()
    except (AttributeError, OSError, ValueError):
        initial_position = None

    response = None
    last_exception = None
    last_cause = None
    for attempt in range(RETRY_ATTEMPTS):
        if attempt > 0 and initial_position is None:
            break
        if initial_position is not None:
            try:
                stream.seek(initial_position)
            except (AttributeError, OSError, ValueError):
                initial_position = None
                if attempt > 0:
                    break
        try:
            response = None
            response = requests.post(
                S2T_API_URL,
                files=files,
                data=payload,
                headers=headers,
                timeout=API_TIMEOUT_SECONDS,
            )
            last_exception = None
        except requests.exceptions.Timeout as exc:
            last_exception = ServiceError("调用语音转录服务超时", 504)
            last_cause = exc
        except requests.exceptions.RequestException as exc:
            last_exception = ServiceError(
                f"无法连接语音转录服务: {type(exc).__name__}", 502
            )
            last_cause = exc
        else:
            if 200 <= response.status_code < 300:
                break
            if response.status_code not in RETRYABLE_STATUS_CODES:
                break

        if initial_position is None:
            break
        if attempt < RETRY_ATTEMPTS - 1:
            time.sleep(_retry_delay(attempt))

    if response is None:
        if last_exception is not None:
            raise last_exception from last_cause
        raise ServiceError("语音转录服务请求失败", 502)
    if not 200 <= response.status_code < 300:
        details = _extract_api_error_message(response)
        raise ServiceError(
            f"语音转录服务返回错误 {response.status_code}: {details}", 502
        )

    try:
        data = response.json()
    except (ValueError, TypeError) as exc:
        raise ServiceError("语音转录服务返回了无效 JSON", 502) from exc
    transcription = data.get("text") if isinstance(data, dict) else None
    if not isinstance(transcription, str) or not transcription.strip():
        raise ServiceError("语音转录服务未能识别出有效文本", 502)
    return transcription.strip()


# =============================================================
# --- Web UI 页面服务路由 ---
# =============================================================
@app.route("/")
def index():
    s2t_configured = bool(S2T_API_KEY and S2T_MODEL and _is_valid_http_url(S2T_API_URL))
    calibration_configured = _opt_configuration_error(CALIBRATION_MODEL, "校准") is None
    summary_configured = _opt_configuration_error(SUMMARY_MODEL, "摘要") is None
    notes_configured = _opt_configuration_error(NOTES_MODEL, "笔记生成") is None
    return render_template(
        "index.html",
        max_upload_bytes=MAX_UPLOAD_BYTES,
        max_upload_mb=MAX_UPLOAD_MB,
        max_text_chars=MAX_TEXT_CHARS,
        s2t_configured=s2t_configured,
        calibration_configured=calibration_configured,
        summary_configured=summary_configured,
        notes_configured=notes_configured,
        repository_url=REPOSITORY_URL,
    )


@app.route("/style.css")
def legacy_style():
    """Keep the pre-namespaced stylesheet URL working for existing bookmarks."""
    return app.send_static_file("style.css")


@app.route("/script.js")
def legacy_script():
    return app.send_static_file("script.js")


@app.route("/favicon.svg")
def legacy_favicon():
    return app.send_static_file("favicon.svg")


@app.route("/healthz")
def healthcheck():
    calibration_configured = _opt_configuration_error(CALIBRATION_MODEL, "校准") is None
    summary_configured = _opt_configuration_error(SUMMARY_MODEL, "摘要") is None
    notes_configured = _opt_configuration_error(NOTES_MODEL, "笔记生成") is None
    return jsonify(
        {
            "status": "ok",
            "s2t_configured": bool(
                S2T_API_KEY and S2T_MODEL and _is_valid_http_url(S2T_API_URL)
            ),
            "optimization_configured": any(
                (calibration_configured, summary_configured, notes_configured)
            ),
            "calibration_configured": calibration_configured,
            "summary_configured": summary_configured,
            "notes_configured": notes_configured,
            "max_upload_mb": MAX_UPLOAD_MB,
            "max_text_chars": MAX_TEXT_CHARS,
        }
    )


@app.after_request
def add_security_headers(response):
    """Apply baseline browser hardening without changing API payloads."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "font-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    )
    return response


# =============================================================
# --- OpenAI 兼容 API 路由 ---
# =============================================================
@app.before_request
def check_openai_auth():
    if request.path.startswith("/v1/"):
        if not API_ACCESS_TOKEN:
            print("警告: API被调用，但环境变量 API_ACCESS_TOKEN 未设置。拒绝访问。")
            return jsonify(
                {
                    "error": {
                        "message": "API access is not configured on the server.",
                        "type": "server_error",
                    }
                }
            ), 503
        auth_header = request.headers.get("Authorization", "")
        scheme, separator, token = auth_header.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token.strip():
            return jsonify(
                {
                    "error": {
                        "message": "Authorization header is missing or invalid.",
                        "type": "invalid_request_error",
                    }
                }
            ), 401
        if not hmac.compare_digest(
            token.strip().encode("utf-8", "surrogatepass"),
            API_ACCESS_TOKEN.encode("utf-8", "surrogatepass"),
        ):
            return jsonify(
                {
                    "error": {
                        "message": "Incorrect API key provided.",
                        "type": "invalid_request_error",
                    }
                }
            ), 401


@app.route("/v1/models", methods=["GET"])
def list_models():
    return jsonify(
        {
            "object": "list",
            "data": [
                {
                    "id": MODEL_CALIBRATE,
                    "object": "model",
                    "created": MODEL_CREATED_AT,
                    "owned_by": "Qianxia666",
                },
                {
                    "id": MODEL_SUMMARIZE,
                    "object": "model",
                    "created": MODEL_CREATED_AT,
                    "owned_by": "Qianxia666",
                },
            ],
        }
    )


@app.route("/v1/audio/transcriptions", methods=["POST"])
def openai_audio_transcriptions():
    if "file" not in request.files:
        return jsonify(
            {
                "error": {
                    "message": "No audio file was provided.",
                    "type": "invalid_request_error",
                }
            }
        ), 400
    audio_file = request.files["file"]
    model_requested = request.form.get("model")
    response_format = request.form.get("response_format") or "json"
    if response_format not in {"json", "text", "verbose_json"}:
        return jsonify(
            {
                "error": {
                    "message": (
                        f"Response format '{response_format}' is not supported. "
                        "Use 'json', 'text', or 'verbose_json'."
                    ),
                    "type": "invalid_request_error",
                }
            }
        ), 400
    if not model_requested or model_requested not in [MODEL_CALIBRATE, MODEL_SUMMARIZE]:
        message = f"Model '{model_requested}' is not supported. Use '{MODEL_CALIBRATE}' or '{MODEL_SUMMARIZE}'."
        return jsonify(
            {"error": {"message": message, "type": "invalid_request_error"}}
        ), 400

    try:
        raw_transcription = _transcribe_audio_file(audio_file)
    except ServiceError as exc:
        if exc.status_code in {400, 413}:
            error_type = "invalid_request_error"
            error_code = "file_too_large" if exc.status_code == 413 else "invalid_file"
        else:
            error_type = "upstream_error"
            error_code = "s2t_failed"
        return jsonify(
            {
                "error": {
                    "message": str(exc),
                    "type": error_type,
                    "code": error_code,
                }
            }
        ), exc.status_code

    calibrated_text, opt_message, is_calibrated = _perform_text_optimization(
        raw_transcription
    )
    final_response = {"text": calibrated_text if is_calibrated else raw_transcription}

    if model_requested == MODEL_CALIBRATE:
        if not is_calibrated:
            final_response["x_warning"] = {
                "code": "calibration_failed",
                "message": f"Text optimization failed. Returning raw transcription. Reason: {opt_message}",
            }
    else:
        summary_source = calibrated_text if is_calibrated else raw_transcription
        summary_result = _perform_summarization(summary_source)
        if summary_result["status"] == "success":
            final_response["text"] = summary_result["summary"]
            if not is_calibrated:
                final_response["x_warning"] = {
                    "code": "calibration_failed_in_summary_workflow",
                    "message": (
                        "Calibration failed. Summary was generated from the raw "
                        f"transcription. Reason: {opt_message}"
                    ),
                }
        else:
            calibration_detail = (
                "" if is_calibrated else f" Calibration also failed: {opt_message}."
            )
            final_response["x_warning"] = {
                "code": "summarization_failed",
                "message": (
                    "Summarization failed. Returning the best available "
                    f"transcription. Reason: {summary_result['message']}."
                    f"{calibration_detail}"
                ),
            }

    if response_format == "text":
        return Response(final_response["text"], mimetype="text/plain")
    return jsonify(final_response)


# =============================================================
# ---  Web UI 数据接口路由API ---
# =============================================================
@app.route("/api/transcribe", methods=["POST"])
def transcribe_and_optimize_audio():
    print("\n--- [Transcribe] 请求开始 ---")
    audio_file = request.files.get("audio_file")
    if not audio_file:
        print("[Transcribe] 错误: 请求中缺少音频文件。")
        return jsonify({"error": "缺少上传的音频文件"}), 400

    try:
        print(f"[Transcribe] 正在调用 S2T API: {S2T_API_URL}")
        start_time = time.time()
        raw_transcription = _transcribe_audio_file(audio_file)
        end_time = time.time()
        print(f"[Transcribe] S2T API 响应完毕. 耗时: {end_time - start_time:.2f} 秒.")
        print("[Transcribe] S2T 文本获取成功.")
    except ServiceError as exc:
        print(f"[Transcribe] 错误: {exc}")
        return jsonify({"error": str(exc)}), exc.status_code

    print("[Transcribe] 正在调用文本优化...")
    final_transcription, opt_message, is_calibrated = _perform_text_optimization(
        raw_transcription
    )
    print(f"[Transcribe] 文本优化完成. 状态: {opt_message}")

    if is_calibrated:
        final_status_message = "转录完成，校准成功！"
    elif opt_message.startswith("校准已跳过"):
        final_status_message = f"转录完成（{opt_message}）"
    else:
        final_status_message = f"转录完成，但{opt_message}"

    print("[Transcribe] 请求处理完毕，正在返回结果。")
    return jsonify(
        {
            "status": "success",
            "transcription": final_transcription,
            "raw_transcription": raw_transcription,
            "calibration_message": final_status_message,
            "is_calibrated": is_calibrated,
        }
    )


@app.route("/api/recalibrate", methods=["POST"])
def recalibrate_text():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "raw_transcription" not in data:
        return jsonify({"error": "请求体无效或缺少 raw_transcription 字段"}), 400
    try:
        raw_text = _validate_text(data.get("raw_transcription"), "需要重新校准的文本")
    except ServiceError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    calibrated_text, calibration_status_msg, calibration_success = (
        _perform_text_optimization(raw_text)
    )
    return jsonify(
        {
            "status": "success",
            "transcription": calibrated_text,
            "calibration_message": calibration_status_msg,
            "is_calibrated": calibration_success,
        }
    )


@app.route("/api/summarize", methods=["POST"])
def summarize_text():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "text_to_summarize" not in data:
        return jsonify({"error": "请求体无效或缺少 'text_to_summarize' 字段"}), 400
    try:
        text = _validate_text(data.get("text_to_summarize"), "待总结的文本")
    except ServiceError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    result = _perform_summarization(text)
    if result["status"] == "success":
        return jsonify({"summary": result["summary"]})
    return jsonify({"error": result["message"]}), result.get("status_code", 502)


@app.route("/api/generatenote", methods=["POST"])
def generate_notes():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "text_to_process" not in data:
        return jsonify({"error": "请求体无效或缺少 'text_to_process' 字段"}), 400

    try:
        text = _validate_text(data.get("text_to_process"), "待处理的文本")
    except ServiceError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    print(f"收到笔记生成请求，文本长度: {len(text)} 字符")

    result = _perform_notes_generation(text)
    if result["status"] == "success":
        return jsonify({"status": "success", "notes": result["notes"]})
    return jsonify({"error": result["message"]}), result.get("status_code", 502)


@app.errorhandler(413)
def request_entity_too_large(_error):
    if request.path.startswith("/v1/"):
        return jsonify(
            {
                "error": {
                    "message": f"Uploaded file exceeds the {MAX_UPLOAD_MB} MB limit.",
                    "type": "invalid_request_error",
                    "code": "file_too_large",
                }
            }
        ), 413
    return jsonify({"error": f"上传文件过大，最大支持 {MAX_UPLOAD_MB} MB"}), 413


@app.errorhandler(404)
def api_not_found(_error):
    if request.path.startswith(("/api/", "/v1/")):
        return jsonify(
            {
                "error": {
                    "message": "The requested API endpoint was not found.",
                    "type": "invalid_request_error",
                }
            }
        ), 404
    return _error


@app.errorhandler(405)
def api_method_not_allowed(_error):
    if request.path.startswith(("/api/", "/v1/")):
        return jsonify(
            {
                "error": {
                    "message": "The HTTP method is not allowed for this endpoint.",
                    "type": "invalid_request_error",
                }
            }
        ), 405
    return _error


@app.errorhandler(400)
def api_bad_request(_error):
    if request.path.startswith(("/api/", "/v1/")):
        return jsonify(
            {
                "error": {
                    "message": "The request could not be parsed.",
                    "type": "invalid_request_error",
                }
            }
        ), 400
    return _error


@app.errorhandler(500)
def api_internal_error(_error):
    """Keep API routes JSON-shaped even when an unexpected exception escapes."""
    if request.path.startswith(("/api/", "/v1/")):
        return jsonify(
            {
                "error": {
                    "message": "The server encountered an internal error.",
                    "type": "server_error",
                }
            }
        ), 500
    return _error


# --- 主程序启动入口 ---
if __name__ == "__main__":
    # 启动时进行配置检查
    print("--- S2T 配置检查 ---")
    if not S2T_API_KEY:
        print("警告: 环境变量 S2T_API_KEY 未设置。")
    if not _is_valid_http_url(S2T_API_URL):
        print(f"警告: 环境变量 S2T_API_URL 格式不正确: {S2T_API_URL}。")

    print("\n--- OPT 配置检查 ---")
    dedicated_models_configured = any((CALIBRATION_MODEL, SUMMARY_MODEL, NOTES_MODEL))
    opt_configured_for_check = bool(
        OPT_API_KEY or OPT_MODEL or dedicated_models_configured
    )
    if opt_configured_for_check:
        if not OPT_API_KEY:
            print("警告: 环境变量 OPT_API_KEY 未设置。文本优化功能将无法使用。")
        if not _is_valid_http_url(OPT_API_URL):
            print(f"警告: 环境变量 OPT_API_URL ({OPT_API_URL}) 无效或格式不正确。")
        if OPT_API_KEY and not OPT_MODEL and not dedicated_models_configured:
            print("警告: 已设置 OPT_API_KEY 但未设置任何文本处理模型。")

        # 检查各功能的专用模型配置
        print("\n--- 功能专用模型配置检查 ---")
        calibration_custom = _text_env("CALIBRATION_MODEL") is not None
        summary_custom = _text_env("SUMMARY_MODEL") is not None
        notes_custom = _text_env("NOTES_MODEL") is not None

        if calibration_custom:
            print(f"✓ 校准功能使用专用模型: {CALIBRATION_MODEL}")
        else:
            print(f"✓ 校准功能使用默认模型: {CALIBRATION_MODEL} (来自OPT_MODEL)")

        if summary_custom:
            print(f"✓ 摘要功能使用专用模型: {SUMMARY_MODEL}")
        else:
            print(f"✓ 摘要功能使用默认模型: {SUMMARY_MODEL} (来自OPT_MODEL)")

        if notes_custom:
            print(f"✓ 笔记功能使用专用模型: {NOTES_MODEL}")
        else:
            print(f"✓ 笔记功能使用默认模型: {NOTES_MODEL} (来自OPT_MODEL)")
    else:
        print("提示: OPT服务未配置，校准、总结和笔记生成功能将不可用。")

    print("\n--- API 封装功能检查 ---")
    if not API_ACCESS_TOKEN:
        print(
            "警告: 环境变量 API_ACCESS_TOKEN 未设置或为空。API封装功能将无法通过认证。"
        )
    else:
        print("API封装功能已启用。")

    print("\n--------------------\n")
    print(f"服务器正在启动，监听 http://0.0.0.0:{SERVER_PORT}")
    serve(app, host="0.0.0.0", port=SERVER_PORT)
