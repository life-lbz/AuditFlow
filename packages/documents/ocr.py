"""OCR 与版式文本获取（文档 §7.1 的 OCR 环节）。

分层策略：
1. 有文本层（PDF/文本文件）→ 直接使用，无需 OCR。
2. 纯图片 → 交给 OCR 后端。
   - stub_sidecar：读取与图片同目录的 .txt sidecar（合成数据提供，等价于「完美 OCR」）
   - remote_vlm  ：调用远端 VLM/OCR 服务（AutoDL 接入点，见 RemoteVlmOcr）
3. 任何后端失败都返回空文本并带错误标记，不抛出，交由上层判定材料不完整。
"""

from __future__ import annotations

import base64
import functools
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from ..core.config import get_settings
from .parser import ParsedDocument


@dataclass
class OcrResult:
    pages: list[str]
    backend: str
    degraded: bool = False
    error: str | None = None


class OcrBackend(ABC):
    name = "base"

    @abstractmethod
    async def recognize(self, parsed: ParsedDocument, sidecar_text: str | None = None) -> OcrResult:
        ...


class TextLayerOcr(OcrBackend):
    """文本层直读（PDF/文本文件）。"""

    name = "text_layer"

    async def recognize(self, parsed: ParsedDocument, sidecar_text: str | None = None) -> OcrResult:
        return OcrResult(pages=list(parsed.pages), backend=self.name)


class SidecarOcr(OcrBackend):
    """图片 OCR 替身：使用随附 sidecar 文本。

    合成数据集为每张票据图片准备 .txt 文本，等价于把「OCR/视觉识别」这一步的
    结果固定下来，使非模型环节（规则、工具、编排、验证）可以确定性验证。
    接入真实 OCR 后，这一后端整体替换为 RemoteVlmOcr。
    """

    name = "stub_sidecar"

    async def recognize(self, parsed: ParsedDocument, sidecar_text: str | None = None) -> OcrResult:
        if sidecar_text:
            return OcrResult(pages=[sidecar_text], backend=self.name)
        return OcrResult(
            pages=[""],
            backend=self.name,
            degraded=True,
            error="no_ocr_text_available",
        )


class RemoteVlmOcr(OcrBackend):
    """远端视觉模型 OCR（AutoDL 接入点）。

    MODEL-TODO: 真实环境可替换为 PaddleOCR / dots.ocr / Qwen-VL 等；
    接口保持 (image bytes) -> text，业务层无需改动。
    """

    name = "remote_vlm"

    def __init__(self, base_url: str, api_key: str = "", model: str = "", timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model or "vlm-ocr"
        self.timeout = timeout

    async def recognize(self, parsed: ParsedDocument, sidecar_text: str | None = None) -> OcrResult:
        if not parsed.page_images or parsed.page_images[0] is None:
            return OcrResult(pages=list(parsed.pages), backend=self.name)
        image_b64 = base64.b64encode(parsed.page_images[0]).decode("ascii")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": [
                    {"type": "text", "text": "请逐行输出图片中的全部文字，保持原始顺序，不要解释。"},
                    {"type": "image_url", "image_url": {"url": f"data:{parsed.mime};base64,{image_b64}"}},
                ]},
            ],
            "temperature": 0.0,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
            text = data["choices"][0]["message"]["content"]
            return OcrResult(pages=[text], backend=self.name)
        except Exception as exc:  # noqa: BLE001 - OCR 失败不应中断整条流程
            return OcrResult(pages=[""], backend=self.name, degraded=True, error=str(exc))


@functools.lru_cache(maxsize=1)
def get_ocr_backend() -> OcrBackend:
    settings = get_settings()
    provider = (settings.model_provider or "stub").strip().lower()
    if provider in {"openai_compatible", "openai", "vllm", "remote"} and settings.model_base_url:
        return RemoteVlmOcr(
            base_url=settings.model_base_url,
            api_key=settings.model_api_key,
            model=settings.vision_model_name or settings.model_name,
            timeout=settings.ocr_timeout_secs,
        )
    return SidecarOcr()


def reset_ocr_backend() -> None:
    get_ocr_backend.cache_clear()
