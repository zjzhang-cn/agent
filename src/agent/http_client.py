"""
HTTP客户端工具模块。
提供构建OpenAI HTTP客户端的功能。
"""
import os
from typing import Optional

import httpx

from .config import get_config_value, parse_bool


def build_openai_http_client() -> Optional[httpx.Client]:
    """根据环境变量构建 OpenAI 的 HTTP 客户端（支持自签名证书信任）。"""
    verify_ssl = parse_bool(
        get_config_value("OPENAI_SSL_VERIFY", "OPENAI_TLS_VERIFY"),
        default=True,
    )
    cert_path = get_config_value("OPENAI_CA_BUNDLE", "SSL_CERT_FILE")

    if cert_path:
        cert_path = os.path.abspath(os.path.expanduser(cert_path))
        if not os.path.isfile(cert_path):
            raise ValueError(f"OPENAI_CA_BUNDLE 指定的证书文件不存在: {cert_path}")

    if not verify_ssl:
        print("警告: 已禁用 OpenAI TLS 证书校验（OPENAI_SSL_VERIFY=false），仅建议在内网调试场景使用。")
        return httpx.Client(verify=False)

    if cert_path:
        print(f"已加载 OpenAI CA 证书: {cert_path}")
        return httpx.Client(verify=cert_path)

    return None