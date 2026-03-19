"""
错误处理和日志模块。
提供统一的错误格式化、日志记录和异常处理功能。
"""
import json
import logging
import sys
import traceback
from typing import Any, Dict, Optional

# 配置日志
logger = logging.getLogger("agent")
logger.setLevel(logging.INFO)

# 避免重复添加处理器
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
    )
    logger.addHandler(handler)


class AgentError(Exception):
    """Agent 基础异常类"""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ToolExecutionError(AgentError):
    """工具执行异常"""
    pass


class ConfigurationError(AgentError):
    """配置异常"""
    pass


def format_exception_details(error: Exception) -> str:
    """格式化异常详情，便于定位 OpenAI 调用问题。

    增强版本：支持更多异常类型，提供更结构化的错误信息。
    """
    error_type = type(error).__name__
    error_module = type(error).__module__

    # 确定错误标题
    if error_module.startswith("openai"):
        title = "调用 OpenAI 接口时发生异常"
    elif isinstance(error, AgentError):
        title = f"Agent 异常: {error_type}"
    else:
        title = "发生异常"

    lines = [
        f"{title}:",
        f"异常类型: {error_type}",
        f"异常信息: {str(error)}",
    ]

    # OpenAI 相关属性
    for attr, label in (
        ("status_code", "HTTP 状态码"),
        ("request_id", "请求 ID"),
        ("code", "错误码"),
        ("param", "错误参数"),
        ("type", "错误类型"),
    ):
        value = getattr(error, attr, None)
        if value not in (None, ""):
            lines.append(f"{label}: {value}")

    # 响应体信息
    body = getattr(error, "body", None)
    if body not in (None, ""):
        if isinstance(body, (dict, list)):
            try:
                body_str = json.dumps(body, ensure_ascii=False, indent=2)
                lines.append(f"响应体: {body_str}")
            except:
                lines.append(f"响应体: {body}")
        else:
            lines.append(f"响应体: {body}")

    # 响应对象
    response = getattr(error, "response", None)
    if response is not None:
        response_status = getattr(response, "status_code", None)
        if response_status not in (None, ""):
            lines.append(f"响应状态码: {response_status}")

    # 原始原因
    if error.__cause__:
        lines.append(f"原始原因: {type(error.__cause__).__name__}: {error.__cause__}")

    # 自定义详情（针对 AgentError）
    if isinstance(error, AgentError) and error.details:
        lines.append("错误详情:")
        for key, value in error.details.items():
            lines.append(f"  {key}: {value}")

    # 堆栈跟踪
    traceback_text = traceback.format_exc().strip()
    if traceback_text and traceback_text != "NoneType: None":
        lines.append("Traceback:")
        lines.append(traceback_text)

    return "\n".join(lines)


def log_exception(error: Exception, context: Optional[str] = None) -> None:
    """记录异常到日志

    Args:
        error: 异常对象
        context: 上下文信息，描述异常发生的位置
    """
    error_details = format_exception_details(error)
    if context:
        logger.error(f"{context}\n{error_details}")
    else:
        logger.error(error_details)


def log_tool_call(tool_name: str, arguments: Dict[str, Any], success: bool = True,
                 result: Optional[str] = None, error: Optional[Exception] = None) -> None:
    """记录工具调用日志

    Args:
        tool_name: 工具名称
        arguments: 工具参数
        success: 是否成功
        result: 执行结果（可选）
        error: 异常对象（可选）
    """
    import json

    log_data = {
        "tool": tool_name,
        "arguments": arguments,
        "success": success,
        "timestamp": logging.Formatter().formatTime(logging.makeLogRecord({})),
    }

    if result is not None:
        log_data["result"] = result[:500] + "..." if len(result) > 500 else result

    if error is not None:
        log_data["error"] = {
            "type": type(error).__name__,
            "message": str(error),
        }

    try:
        log_message = f"工具调用: {json.dumps(log_data, ensure_ascii=False)}"
        if success:
            logger.info(log_message)
        else:
            logger.error(log_message)
    except:
        # 如果 JSON 序列化失败，使用简单格式
        logger.info(f"工具调用: {tool_name} - {'成功' if success else '失败'}")


def log_conversation(user_input: str, assistant_reply: str,
                    log_file_path: Optional[str] = None) -> None:
    """记录对话到日志文件，同时输出到结构化日志

    Args:
        user_input: 用户输入
        assistant_reply: AI 回复
        log_file_path: 可选的文件路径，如果提供则同时写入文件
    """
    # 记录到结构化日志
    logger.info(f"用户输入: {user_input[:200]}...")
    logger.info(f"AI 回复: {assistant_reply[:200]}...")

    # 同时写入文件（向后兼容）
    if log_file_path:
        from datetime import datetime
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(log_file_path, "a", encoding="utf-8") as log_file:
                log_file.write(f"[{timestamp}] 用户: {user_input}\n")
                log_file.write(f"[{timestamp}] AI: {assistant_reply}\n")
                log_file.write("-" * 50 + "\n")
        except Exception as error:
            logger.error(f"写入对话日志文件失败: {error}")