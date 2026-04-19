"""
Small OpenAI integration for editorial helpers.

The browser never receives the API key. Calls are made on demand by the
backend through the Responses API.
"""
from __future__ import annotations

import os
from typing import Any

from aiohttp import ClientSession

OPENAI_API_URL = "https://api.openai.com/v1/responses"
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")


class OpenAIConfigError(RuntimeError):
    pass


class OpenAIEmptyResponseError(RuntimeError):
    pass


def _compact_tweets(tweets: list[dict[str, Any]], limit: int = 60) -> str:
    rows = []
    for idx, t in enumerate(tweets[:limit], 1):
        metrics = (
            f"replies={t.get('replies', '0')}, "
            f"rts={t.get('retweets', '0')}, "
            f"likes={t.get('likes', '0')}, "
            f"views={t.get('views', '') or 'n/d'}"
        )
        rows.append(
            "\n".join(
                [
                    f"{idx}. {t.get('author_name', '')} {t.get('author_handle', '')}".strip(),
                    f"Metricas: {metrics}",
                    f"Texto: {str(t.get('text', '')).strip()[:900]}",
                    f"URL: {t.get('url', '')}",
                ]
            )
        )
    return "\n\n".join(rows)


async def summarize_column(tweets: list[dict[str, Any]], column_name: str = "") -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise OpenAIConfigError("OPENAI_API_KEY ausente no backend. Configure a variável de ambiente para usar o resumo IA.")
    if not tweets:
        return "Sem tweets suficientes para resumir esta coluna."

    prompt = f"""
Voce e um editor de esporte trabalhando em tempo real.
Resuma em portugues, de forma curta e acionavel, os tweets atuais da coluna "{column_name or 'sem nome'}".

Entregue:
- 3 a 5 bullets com os principais assuntos;
- sinais de pauta ou controversia;
- o que merece monitoramento nos proximos minutos;
- nao invente fatos que nao estejam nos tweets.

Tweets:
{_compact_tweets(tweets)}
""".strip()

    payload = {
        "model": OPENAI_MODEL,
        "input": prompt,
        "max_output_tokens": 700,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with ClientSession() as session:
        async with session.post(OPENAI_API_URL, json=payload, headers=headers, timeout=45) as resp:
            try:
                data = await resp.json(content_type=None)
            except Exception:
                body = await resp.text()
                raise RuntimeError(f"OpenAI retornou resposta inválida: {body[:160]}")
            if resp.status >= 400:
                msg = data.get("error", {}).get("message") if isinstance(data, dict) else ""
                raise RuntimeError(msg or f"OpenAI retornou HTTP {resp.status}")

    text = _extract_response_text(data)
    if not text:
        raise OpenAIEmptyResponseError("A OpenAI respondeu sem texto para esta coluna. Tente novamente ou reduza a quantidade de tweets enviada.")
    return text


def _extract_response_text(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    if isinstance(data.get("output_text"), str) and data["output_text"].strip():
        return data["output_text"].strip()
    parts: list[str] = []
    for item in data.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) or []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
            elif isinstance(text, dict) and isinstance(text.get("value"), str):
                parts.append(text["value"].strip())
    return "\n".join(p for p in parts if p).strip()
