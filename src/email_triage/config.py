"""配置加载与校验。配置里不允许出现凭据。"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .models import Category

_SECRET_KEYS = {"password", "passwd", "token", "secret", "api_key", "apikey", "credential", "credentials"}
_SECRET_SUFFIXES = ("_password", "_passwd", "_token", "_secret", "_api_key", "_apikey", "_credential", "_credentials")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Budget:
    max_model_calls: int = 8
    per_call_timeout_s: float = 20.0
    wall_clock_timeout_s: float = 90.0
    max_body_bytes_per_message: int = 32768
    max_total_input_bytes: int = 262144
    max_tokens: int = 200


@dataclass(frozen=True)
class ModelConfig:
    base_url: str = "http://127.0.0.1:8000/v1"
    model: str = "local-model"
    temperature: float = 0.0
    reasoning_effort: Optional[str] = None
    use_env_proxy: bool = False
    api_key_env: Optional[str] = None


@dataclass(frozen=True)
class TriageConfig:
    categories: List[Category]
    budget: Budget
    model: ModelConfig
    task_instruction: str

    def category_names(self) -> List[str]:
        return [c.name for c in self.categories]

    def categories_payload(self) -> List[Dict[str, str]]:
        return [{"name": c.name, "description": c.description} for c in self.categories]


def _looks_secret(key) -> bool:
    lowered = str(key).lower()
    return lowered in _SECRET_KEYS or lowered.endswith(_SECRET_SUFFIXES)


def _reject_secrets(node, path="config"):
    if isinstance(node, dict):
        for key, value in node.items():
            if _looks_secret(key):
                raise ConfigError(
                    "%s.%s 看起来是凭据字段；凭据只能由运行环境注入，不能写进配置" % (path, key)
                )
            _reject_secrets(value, "%s.%s" % (path, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _reject_secrets(value, "%s[%d]" % (path, index))


def _require_positive(value, name, allow_zero=False):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ConfigError("%s 必须是数字" % name)
    if value < 0 or (value == 0 and not allow_zero):
        raise ConfigError("%s 必须是正数" % name)


def load_config(path: str) -> TriageConfig:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    _reject_secrets(raw)

    raw_categories = raw.get("categories") or []
    if len(raw_categories) < 2:
        raise ConfigError("至少需要两个类别，否则分类没有判别意义")
    categories: List[Category] = []
    seen = set()
    for item in raw_categories:
        name = str(item.get("name", "")).strip()
        if not name:
            raise ConfigError("类别缺少 name")
        if name in seen:
            raise ConfigError("类别重复: %s" % name)
        seen.add(name)
        categories.append(Category(name=name, description=str(item.get("description", "")).strip()))

    budget_raw = raw.get("budget") or {}
    budget = Budget(
        max_model_calls=int(budget_raw.get("max_model_calls", 8)),
        per_call_timeout_s=float(budget_raw.get("per_call_timeout_s", 20.0)),
        wall_clock_timeout_s=float(budget_raw.get("wall_clock_timeout_s", 90.0)),
        max_body_bytes_per_message=int(budget_raw.get("max_body_bytes_per_message", 32768)),
        max_total_input_bytes=int(budget_raw.get("max_total_input_bytes", 262144)),
        max_tokens=int(budget_raw.get("max_tokens", 200)),
    )
    for name in ("max_model_calls", "per_call_timeout_s", "wall_clock_timeout_s",
                 "max_body_bytes_per_message", "max_total_input_bytes", "max_tokens"):
        _require_positive(getattr(budget, name), "budget.%s" % name)

    model_raw = raw.get("model") or {}
    model = ModelConfig(
        base_url=str(os.environ.get("EMAIL_TRIAGE_BASE_URL")
                     or model_raw.get("base_url", "http://127.0.0.1:8000/v1")).rstrip("/"),
        model=str(os.environ.get("EMAIL_TRIAGE_MODEL") or model_raw.get("model", "local-model")),
        temperature=float(model_raw.get("temperature", 0.0)),
        reasoning_effort=model_raw.get("reasoning_effort", None),
        use_env_proxy=bool(model_raw.get("use_env_proxy", False)),
        api_key_env=model_raw.get("api_key_env") or None,
    )
    if not model.base_url.startswith(("http://", "https://")):
        raise ConfigError("model.base_url 必须是 http(s) URL")
    if model.api_key_env and not str(model.api_key_env).replace("_", "").isalnum():
        raise ConfigError("model.api_key_env 必须是环境变量名（只写变量名，不写密钥本身）")

    task_instruction = str(raw.get("task_instruction", "")).strip()
    if not task_instruction:
        raise ConfigError("缺少 task_instruction：分类规则必须由用户给出")

    return TriageConfig(
        categories=categories,
        budget=budget,
        model=model,
        task_instruction=task_instruction,
    )
