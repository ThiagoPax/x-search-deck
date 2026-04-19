"""
Small OpenAI integration for editorial helpers.

The browser never receives the API key. Calls are made on demand by the
backend through the Responses API.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

OPENAI_API_URL = "https://api.openai.com/v1/responses"
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
SUMMARY_TWEET_LIMIT = 20
SUMMARY_TEXT_LIMIT = 600
log = logging.getLogger(__name__)


class OpenAIConfigError(RuntimeError):
    pass


class OpenAIModelError(RuntimeError):
    pass


class OpenAIRateLimitError(RuntimeError):
    pass


class OpenAITimeoutError(RuntimeError):
    pass


class OpenAIUpstreamError(RuntimeError):
    pass


class OpenAIEmptyResponseError(RuntimeError):
    pass


def _compact_tweets(tweets: list[dict[str, Any]], limit: int = SUMMARY_TWEET_LIMIT) -> str:
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
                    f"Texto: {str(t.get('text', '')).strip()[:SUMMARY_TEXT_LIMIT]}",
                    f"URL: {t.get('url', '')}",
                ]
            )
        )
    return "\n\n".join(rows)


async def summarize_column(tweets: list[dict[str, Any]], column_name: str = "") -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise OpenAIConfigError("OPENAI_API_KEY ausente no backend. Configure a variável de ambiente para usar o resumo IA.")
    model = os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip()
    if not model:
        raise OpenAIConfigError("OPENAI_MODEL está vazio. Defina um modelo válido, como gpt-4.1-mini.")
    if not tweets:
        return "Sem tweets suficientes para resumir esta coluna."
    compact_tweets = tweets[:SUMMARY_TWEET_LIMIT]

    prompt = f"""
Voce e um editor de esporte trabalhando em tempo real.
Resuma em portugues, de forma curta e acionavel, os tweets atuais da coluna "{column_name or 'sem nome'}".

Entregue:
- 3 a 5 bullets com os principais assuntos;
- sinais de pauta ou controversia;
- o que merece monitoramento nos proximos minutos;
- nao invente fatos que nao estejam nos tweets.

Tweets:
{_compact_tweets(compact_tweets)}
""".strip()

    payload = {
        "model": model,
        "input": prompt,
        "max_output_tokens": 700,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout = ClientTimeout(total=45, connect=10, sock_read=35)
    try:
        async with ClientSession(timeout=timeout) as session:
            async with session.post(OPENAI_API_URL, json=payload, headers=headers) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    body = await resp.text()
                    _log_openai_error("invalid_json", resp.status, body[:300])
                    raise OpenAIUpstreamError("A OpenAI retornou uma resposta inválida. Tente novamente em instantes.")
                if resp.status >= 400:
                    _raise_openai_http_error(resp.status, data)
    except asyncio.TimeoutError as exc:
        log.warning("Resumo IA: timeout ao chamar OpenAI model=%s tweets=%s", model, len(compact_tweets))
        raise OpenAITimeoutError("Tempo esgotado ao chamar a OpenAI. Tente novamente em instantes.") from exc
    except ClientError as exc:
        log.warning("Resumo IA: falha de rede ao chamar OpenAI: %s", exc)
        raise OpenAIUpstreamError("Falha de rede ao chamar a OpenAI. Tente novamente em instantes.") from exc

    text = _extract_response_text(data)
    if not text:
        status = data.get("status") if isinstance(data, dict) else None
        finish_reason = _find_first_value(data, "finish_reason")
        log.warning("Resumo IA: resposta sem texto utilizavel status=%s finish_reason=%s", status, finish_reason)
        raise OpenAIEmptyResponseError("A OpenAI respondeu sem texto utilizável para esta coluna. Tente novamente; se persistir, verifique o modelo configurado.")
    return text


def _extract_response_text(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    parts: list[str] = []
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    _collect_response_text(data.get("output"), parts)
    return "\n".join(p for p in parts if p).strip()


def _collect_response_text(value: Any, parts: list[str]) -> None:
    if isinstance(value, list):
        for item in value:
            _collect_response_text(item, parts)
        return
    if not isinstance(value, dict):
        return

    content = value.get("content")
    if isinstance(content, list):
        _collect_response_text(content, parts)

    text = value.get("text")
    if isinstance(text, str) and text.strip():
        parts.append(text.strip())
    elif isinstance(text, dict) and isinstance(text.get("value"), str) and text["value"].strip():
        parts.append(text["value"].strip())

    nested_output = value.get("output")
    if isinstance(nested_output, list):
        _collect_response_text(nested_output, parts)


def _raise_openai_http_error(status: int, data: Any) -> None:
    error = data.get("error", {}) if isinstance(data, dict) else {}
    message = error.get("message") if isinstance(error, dict) else ""
    code = error.get("code") if isinstance(error, dict) else ""
    err_type = error.get("type") if isinstance(error, dict) else ""
    _log_openai_error(err_type or "http_error", status, message, code)

    lowered = f"{message} {code} {err_type}".lower()
    if status == 401:
        raise OpenAIConfigError("OPENAI_API_KEY inválida ou sem permissão para a OpenAI.")
    if status == 429:
        raise OpenAIRateLimitError("Limite de uso da OpenAI atingido. Aguarde um pouco e tente novamente.")
    if status in (400, 404) and ("model" in lowered or "does not exist" in lowered or "access" in lowered):
        raise OpenAIModelError("OPENAI_MODEL inválido ou sem acesso nesta chave. Verifique o modelo configurado no backend.")
    if 500 <= status <= 599:
        raise OpenAIUpstreamError("A OpenAI está indisponível ou retornou erro temporário. Tente novamente em instantes.")
    raise OpenAIUpstreamError(message or f"A OpenAI retornou HTTP {status}.")


def _log_openai_error(kind: str, status: int, message: str = "", code: str = "") -> None:
    safe_message = message or ""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if api_key:
        safe_message = safe_message.replace(api_key, "[redacted]")
    safe_message = safe_message[:500]
    log.warning("Resumo IA: erro OpenAI kind=%s status=%s code=%s message=%s", kind, status, code, safe_message)


def _find_first_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for nested in value.values():
            found = _find_first_value(nested, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_first_value(item, key)
            if found is not None:
                return found
    return None
