"""统一配置管理"""
import os
import json
from pydantic import BaseModel
from typing import Optional


class LLMConfig(BaseModel):
    model: str = "deepseek-v4-flash"
    base_url: str = "http://10.10.10.200:8317/v1"
    api_key: str = "simon123"
    temperature: float = 0.3
    timeout: int = 180


class VLMConfig(BaseModel):
    model: str = "agnes-2.0-flash"
    base_url: str = "http://10.10.10.200:8317/v1"
    api_key: str = "simon123"
    max_tokens: int = 1000
    temperature: float = 0.3


class OCRConfig(BaseModel):
    batch_size: int = 20
    timeout: int = 600
    dpi: int = 150
    lang: str = "ch"


class ProcessingConfig(BaseModel):
    pages_per_chunk: int = 30
    max_ocr_text_length: int = 35000
    context_window: int = 2000
    enable_checkpoint: bool = True
    max_global_review_rounds: int = 3
    fast_mode: bool = False
    max_chunk_summaries: int = 20


class OutputConfig(BaseModel):
    save_individual: bool = True
    save_merged: bool = True
    include_toc: bool = True
    include_concept_index: bool = True
    include_timeline: bool = True


class ObsidianConfig(BaseModel):
    path: str = r"D:\tool\obsidian\20玄学\奇门遁甲\荀爽"


class AppConfig(BaseModel):
    llm: LLMConfig = LLMConfig()
    vlm: VLMConfig = VLMConfig()
    ocr: OCRConfig = OCRConfig()
    processing: ProcessingConfig = ProcessingConfig()
    output: OutputConfig = OutputConfig()
    obsidian: ObsidianConfig = ObsidianConfig()

    @classmethod
    def load(cls, path: str = None) -> "AppConfig":
        if path is None:
            # 始终从项目根目录加载 config.json
            root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            path = os.path.join(root_dir, "config.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls(**data)
        return cls()

    def save(self, path: str = None):
        if path is None:
            root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            path = os.path.join(root_dir, "config.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.model_dump(), f, indent=2, ensure_ascii=False)


_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = AppConfig.load()
    return _config


def set_config(config: AppConfig):
    global _config
    _config = config


def get_llm_config(model_override: str = None, base_url_override: str = None, api_key_override: str = None) -> LLMConfig:
    """获取LLM配置，支持客户端覆盖"""
    config = get_config().llm
    if model_override:
        config.model = model_override
    if base_url_override:
        config.base_url = base_url_override
    if api_key_override:
        config.api_key = api_key_override
    return config


def get_vlm_config() -> VLMConfig:
    return get_config().vlm
