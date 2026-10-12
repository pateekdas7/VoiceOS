"""LLM service tests — runs against mock LLM server.

Validates the OpenAI-compatible API contract that VoiceOS clients depend on:
- Health endpoint
- Model listing
- Non-streaming completions
- Streaming completions (SSE format)
- TTFT measurement
- Error handling
"""

from __future__ import annotations

import json
import time

import httpx
import pytest


# ── Health + models ────────────────────────────────────────────────────────────


def test_health(llm_client: httpx.Client) -> None:
    resp = llm_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_list_models(llm_client: httpx.Client) -> None:
    resp = llm_client.get("/v1/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    assert len(body["data"]) >= 1
    model = body["data"][0]
    assert "id" in model
    assert "object" in model


def test_metrics(llm_client: httpx.Client) -> None:
    resp = llm_client.get("/metrics")
    assert resp.status_code == 200
    assert "voiceos_llm_mock_ready 1" in resp.text


# ── Non-streaming completions ──────────────────────────────────────────────────


def test_completion_non_streaming(llm_client: httpx.Client) -> None:
    payload = {
        "model": "qwen2.5-7b-instruct-fp8",
        "messages": [{"role": "user", "content": "Say hello in Hindi."}],
        "max_tokens": 64,
        "stream": False,
    }
    resp = llm_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert len(body["choices"]) == 1
    choice = body["choices"][0]
    assert choice["message"]["role"] == "assistant"
    assert len(choice["message"]["content"]) > 0
    assert choice["finish_reason"] == "stop"


def test_completion_response_schema(llm_client: httpx.Client) -> None:
    resp = llm_client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen2.5-7b-instruct-fp8",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": False,
        },
    )
    body = resp.json()
    assert "id" in body
    assert "created" in body
    assert "model" in body
    assert "usage" in body
    usage = body["usage"]
    assert "prompt_tokens" in usage
    assert "completion_tokens" in usage
    assert "total_tokens" in usage
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


def test_completion_returns_hindi(llm_client: httpx.Client) -> None:
    resp = llm_client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen2.5-7b-instruct-fp8",
            "messages": [{"role": "user", "content": "नमस्ते"}],
            "stream": False,
        },
    )
    content = resp.json()["choices"][0]["message"]["content"]
    # Mock returns Hindi canned responses — check for Devanagari script
    assert any(ord(c) > 0x0900 for c in content), "Expected Hindi content in response"


# ── Streaming completions ──────────────────────────────────────────────────────


def test_completion_streaming_format(llm_client: httpx.Client) -> None:
    payload = {
        "model": "qwen2.5-7b-instruct-fp8",
        "messages": [{"role": "user", "content": "Say hello."}],
        "stream": True,
    }
    chunks: list[dict] = []
    with llm_client.stream("POST", "/v1/chat/completions", json=payload) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            chunks.append(json.loads(data))

    assert len(chunks) > 0
    for chunk in chunks:
        assert chunk["object"] == "chat.completion.chunk"
        assert len(chunk["choices"]) == 1


def test_completion_streaming_ttft(llm_client: httpx.Client) -> None:
    payload = {
        "model": "qwen2.5-7b-instruct-fp8",
        "messages": [{"role": "user", "content": "Hi."}],
        "stream": True,
    }
    t0 = time.monotonic()
    ttft_ms: float | None = None
    with llm_client.stream("POST", "/v1/chat/completions", json=payload) as resp:
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            if chunk["choices"][0]["delta"].get("content"):
                ttft_ms = (time.monotonic() - t0) * 1000
                break

    assert ttft_ms is not None, "No content chunk received"
    assert ttft_ms < 500, f"TTFT {ttft_ms:.0f}ms exceeds 500ms target"


def test_completion_streaming_complete_text(llm_client: httpx.Client) -> None:
    """Concatenating all streaming chunks should give a non-empty response."""
    payload = {
        "model": "qwen2.5-7b-instruct-fp8",
        "messages": [{"role": "user", "content": "Describe payment options."}],
        "stream": True,
    }
    text = ""
    done = False
    with llm_client.stream("POST", "/v1/chat/completions", json=payload) as resp:
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                done = True
                break
            chunk = json.loads(data)
            content = chunk["choices"][0]["delta"].get("content", "")
            text += content
            # Do NOT break on finish_reason — keep reading until [DONE]

    assert done, "Stream did not end with [DONE]"
    assert len(text) > 10, f"Response too short: {text!r}"


def test_completion_streaming_finish_reason(llm_client: httpx.Client) -> None:
    payload = {
        "model": "qwen2.5-7b-instruct-fp8",
        "messages": [{"role": "user", "content": "Hi."}],
        "stream": True,
    }
    finish_reasons = []
    with llm_client.stream("POST", "/v1/chat/completions", json=payload) as resp:
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            fr = chunk["choices"][0].get("finish_reason")
            if fr:
                finish_reasons.append(fr)

    assert "stop" in finish_reasons


# ── Multi-turn conversation ────────────────────────────────────────────────────


def test_multi_turn_conversation(llm_client: httpx.Client) -> None:
    messages = [
        {"role": "user", "content": "मेरा loan balance कितना है?"},
        {"role": "assistant", "content": "आपका balance ₹50,000 है।"},
        {"role": "user", "content": "Payment kaise karein?"},
    ]
    resp = llm_client.post(
        "/v1/chat/completions",
        json={"model": "qwen2.5-7b-instruct-fp8", "messages": messages, "stream": False},
    )
    assert resp.status_code == 200
    assert len(resp.json()["choices"][0]["message"]["content"]) > 0


# ── Unique request IDs ─────────────────────────────────────────────────────────


def test_each_completion_has_unique_id(llm_client: httpx.Client) -> None:
    ids = []
    for _ in range(3):
        resp = llm_client.post(
            "/v1/chat/completions",
            json={
                "model": "qwen2.5-7b-instruct-fp8",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": False,
            },
        )
        ids.append(resp.json()["id"])
    assert len(set(ids)) == 3, f"Expected unique IDs, got: {ids}"
