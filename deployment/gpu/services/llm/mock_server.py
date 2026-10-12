#!/usr/bin/env python3
"""Mock LLM Server — OpenAI-compatible API for local GPU development.

Implements the OpenAI chat completions API that all VoiceOS LLM clients
consume. Use this in VOICEOS_MODE=dev to avoid requiring vLLM and a GPU.

The mock returns realistic Hindi/Hinglish voice responses with configurable
token-level delays so the full STT→LLM→TTS pipeline can be tested locally.

Endpoints:
    GET  /health                  — vLLM-compatible health probe
    GET  /v1/models               — list available models
    POST /v1/chat/completions     — streaming and non-streaming completions

Usage:
    python3 mock_server.py --port 8000
    VOICEOS_MODE=dev python3 mock_server.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("voiceos.llm.mock")

app = FastAPI(title="VoiceOS Mock LLM", version="0.0.1")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_PORT: int = 8000
_SERVED_MODEL_NAME: str = "qwen2.5-7b-instruct-fp8"
_TOKEN_DELAY_S: float = 0.02  # 20ms/token → ~50 tok/s simulated throughput
_TTFT_DELAY_S: float = 0.06   # 60ms simulated time-to-first-token

# Canned responses for the voice collections domain (Hindi/Hinglish).
# Each response is split into tokens (words) to simulate realistic streaming.
_CANNED_RESPONSES: list[str] = [
    "नमस्ते, मैं Kavya बोल रही हूं। आपका loan balance ₹50,000 है। "
    "क्या आप इस बारे में बात करना चाहेंगे?",
    "मैं समझती हूं। आपकी payment की due date 30 July थी। "
    "क्या आप partial payment कर सकते हैं?",
    "धन्यवाद। हम आपके लिए एक आसान repayment plan बना सकते हैं। "
    "कृपया हमारी team से बात करें।",
    "ठीक है। मैंने आपकी बात note कर ली है। "
    "हम आपको 24 घंटे में callback करेंगे।",
    "आपकी सुविधा के लिए हम flexible EMI options offer करते हैं। "
    "क्या आप अभी payment portal access करना चाहेंगे?",
]

_response_index: int = 0


def _next_response() -> str:
    global _response_index
    resp = _CANNED_RESPONSES[_response_index % len(_CANNED_RESPONSES)]
    _response_index += 1
    return resp


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = _SERVED_MODEL_NAME
    messages: list[ChatMessage]
    max_tokens: int = 256
    temperature: float = 0.7
    stream: bool = False


# ---------------------------------------------------------------------------
# Health + models endpoints (vLLM-compatible)
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/metrics")
async def metrics() -> Response:
    lines = [
        "# HELP voiceos_llm_mock_requests_total Total mock LLM requests",
        "# TYPE voiceos_llm_mock_requests_total counter",
        f"voiceos_llm_mock_requests_total {_response_index}",
        "# HELP voiceos_llm_mock_ready Mock LLM server ready (always 1)",
        "# TYPE voiceos_llm_mock_ready gauge",
        "voiceos_llm_mock_ready 1",
    ]
    return Response(
        content="\n".join(lines) + "\n",
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/v1/models")
async def list_models() -> JSONResponse:
    return JSONResponse(
        {
            "object": "list",
            "data": [
                {
                    "id": _SERVED_MODEL_NAME,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "voiceos-mock",
                }
            ],
        }
    )


# ---------------------------------------------------------------------------
# Chat completions — streaming and non-streaming
# ---------------------------------------------------------------------------


def _make_chunk(content: str, finish_reason: str | None, request_id: str) -> str:
    payload = {
        "id": request_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": _SERVED_MODEL_NAME,
        "choices": [
            {
                "index": 0,
                "delta": {"content": content} if content else {},
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(payload)}\n\n"


async def _stream_response(text: str, request_id: str) -> AsyncIterator[str]:
    await asyncio.sleep(_TTFT_DELAY_S)
    tokens = text.split(" ")
    for i, tok in enumerate(tokens):
        word = tok if i == 0 else " " + tok
        yield _make_chunk(word, None, request_id)
        await asyncio.sleep(_TOKEN_DELAY_S)
    yield _make_chunk("", "stop", request_id)
    yield "data: [DONE]\n\n"


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest) -> Response:
    request_id = f"chatcmpl-mock-{uuid.uuid4().hex[:8]}"
    response_text = _next_response()
    logger.info(
        "Mock completion %s | stream=%s | %d chars",
        request_id,
        request.stream,
        len(response_text),
    )

    if request.stream:
        return StreamingResponse(
            _stream_response(response_text, request_id),
            media_type="text/event-stream",
        )

    await asyncio.sleep(_TTFT_DELAY_S + len(response_text.split()) * _TOKEN_DELAY_S)
    return JSONResponse(
        {
            "id": request_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": _SERVED_MODEL_NAME,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": response_text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": sum(len(m.content.split()) for m in request.messages),
                "completion_tokens": len(response_text.split()),
                "total_tokens": sum(len(m.content.split()) for m in request.messages)
                + len(response_text.split()),
            },
        }
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="VoiceOS Mock LLM Server")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument(
        "--token-delay-ms",
        type=float,
        default=20.0,
        help="Simulated inter-token delay in ms (default 20 → ~50 tok/s)",
    )
    parser.add_argument(
        "--ttft-ms",
        type=float,
        default=60.0,
        help="Simulated time-to-first-token in ms (default 60)",
    )
    parser.add_argument(
        "--model-name",
        default=None,
        help="Model name reported in API responses",
    )
    args = parser.parse_args()

    global _TOKEN_DELAY_S, _TTFT_DELAY_S, _SERVED_MODEL_NAME
    _TOKEN_DELAY_S = args.token_delay_ms / 1000
    _TTFT_DELAY_S = args.ttft_ms / 1000
    if args.model_name:
        _SERVED_MODEL_NAME = args.model_name

    logger.info(
        "Starting mock LLM on %s:%d (TTFT=%.0fms, token=%.0fms)",
        args.host,
        args.port,
        args.ttft_ms,
        args.token_delay_ms,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
