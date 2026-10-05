"""API key 安全存储：仅经系统 keyring 保存，数据库只存 credential_ref。

keyring 后端：Windows Credential Manager / macOS Keychain / Linux Secret Service。
keyring 不可用时（如无桌面环境的 Linux 服务器）抛出明确错误，绝不退化为明文入库。
"""

from __future__ import annotations

import uuid

import keyring

SERVICE_NAME = "onprsbench"


def store_api_key(api_key: str) -> str:
    """保存 API key，返回 credential_ref（写入 Provider 记录）。"""
    if not api_key:
        raise ValueError("api_key 不能为空")
    ref = uuid.uuid4().hex
    keyring.set_password(SERVICE_NAME, ref, api_key)
    return ref


def get_api_key(credential_ref: str) -> str | None:
    """按 credential_ref 读取 API key。"""
    return keyring.get_password(SERVICE_NAME, credential_ref)


def delete_api_key(credential_ref: str) -> None:
    """删除 credential_ref 对应的 API key（忽略不存在的记录）。"""
    try:
        keyring.delete_password(SERVICE_NAME, credential_ref)
    except keyring.errors.PasswordDeleteError:
        pass


def has_credential(credential_ref: str | None) -> bool:
    if not credential_ref:
        return False
    return get_api_key(credential_ref) is not None
