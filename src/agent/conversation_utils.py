"""
对话工具模块。
提供对话结束检测功能。
"""
from typing import Optional


# 结束对话的关键词
END_CONVERSATION_KEYWORDS = [
    "<<再见>>",
    "<<结束>>",
    "<<完成>>",
    "<<结束对话>>",
    "<<MESSAGE_END>>",
    "<<END>>",
]


def should_end_conversation(response_content: Optional[str]) -> bool:
    """根据回复内容判断是否应该结束对话"""
    if not response_content:
        return False
    content_lower = response_content.lower()
    return any(keyword in content_lower for keyword in END_CONVERSATION_KEYWORDS)