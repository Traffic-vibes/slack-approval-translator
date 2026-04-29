import logging
import os
import secrets
import time
from dataclasses import dataclass
from threading import Lock
from typing import Dict

from dotenv import load_dotenv
from openai import OpenAI
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler


load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

REQUIRED_ENV = [
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
]

missing_env = [name for name in REQUIRED_ENV if not os.getenv(name)]
if missing_env:
    raise RuntimeError(
        "Missing required environment variables: " + ", ".join(missing_env)
    )

OPENAI_MODEL = os.environ["OPENAI_MODEL"]
PREVIEW_TTL_SECONDS = 15 * 60

GLOSSARY = {
    "ROAS": "Keep as ROAS.",
    "CPA": "Keep as CPA.",
    "CPI": "Keep as CPI.",
    "CTR": "Keep as CTR.",
    "CPC": "Keep as CPC.",
    "CPM": "Keep as CPM.",
    "GEO": "Use GEO for country or market targeting.",
    "geo": "Use GEO for country or market targeting.",
    "creative": "Use creative.",
    "creatives": "Use creatives.",
    "build": "Use build.",
    "campaign": "Use campaign.",
    "adset": "Use adset.",
    "offer": "Use offer.",
    "landing": "Use landing.",
    "pixel": "Use pixel.",
    "event": "Use event.",
    "purchase": "Use purchase.",
    "lead": "Use lead.",
    "install": "Use install.",
    "UA": "Keep as UA for user acquisition.",
    "traffic": "Use traffic.",
}


def glossary_text() -> str:
    return "\n".join(f"- {term}: {instruction}" for term, instruction in GLOSSARY.items())


RU_TO_EN_SYSTEM_PROMPT = f"""
You are a professional translator for an ad-tech and performance marketing team.
Translate Russian text into natural, concise business English.
Preserve meaning, numbers, URLs, Slack mentions, line breaks, and simple formatting.
Use direct business wording; do not add explanations.
Follow this ad-tech glossary:
{glossary_text()}
Return only the translated English text.
""".strip()

EN_TO_RU_SYSTEM_PROMPT = f"""
You are a professional translator for an ad-tech and performance marketing team.
Translate English text into natural Russian suitable for internal business communication.
Preserve meaning, numbers, URLs, Slack mentions, line breaks, and simple formatting.
Keep common ad-tech abbreviations such as ROAS, CPA, CPI, CTR, CPC, CPM, GEO, and UA unchanged.
Use common Russian ad-tech wording for terms such as creative, creatives, build, campaign, adset,
offer, landing, pixel, event, purchase, lead, install, and traffic.
Return only the translated Russian text.
""".strip()


@dataclass
class PendingTranslation:
    user_id: str
    channel_id: str
    translated_text: str
    created_at: float


pending_translations: Dict[str, PendingTranslation] = {}
pending_translations_lock = Lock()

openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
app = App(token=os.environ["SLACK_BOT_TOKEN"])


def cleanup_pending_translations() -> None:
    cutoff = time.time() - PREVIEW_TTL_SECONDS
    with pending_translations_lock:
        expired_tokens = [
            token
            for token, item in pending_translations.items()
            if item.created_at < cutoff
        ]
        for token in expired_tokens:
            pending_translations.pop(token, None)


def translate(text: str, system_prompt: str) -> str:
    response = openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
    )

    translated = response.choices[0].message.content
    if not translated:
        raise RuntimeError("OpenAI returned an empty translation.")

    return translated.strip()


def slack_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def truncate_for_slack_block(text: str, limit: int = 2800) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 32].rstrip() + "\n\n[Preview truncated]"


def preview_blocks(translated_text: str, token: str) -> list:
    preview_text = slack_escape(truncate_for_slack_block(translated_text))
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*English preview:*\n{preview_text}",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Send"},
                    "style": "primary",
                    "action_id": "tr_send",
                    "value": token,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Cancel"},
                    "style": "danger",
                    "action_id": "tr_cancel",
                    "value": token,
                },
            ],
        },
    ]


@app.command("/tr")
def handle_translate_to_english(ack, body, respond):
    ack()
    text = (body.get("text") or "").strip()
    if not text:
        respond(
            response_type="ephemeral",
            text="Usage: /tr Russian text to translate",
        )
        return

    cleanup_pending_translations()

    try:
        translated_text = translate(text, RU_TO_EN_SYSTEM_PROMPT)
    except Exception:
        logger.exception("Failed to translate /tr input")
        respond(
            response_type="ephemeral",
            text="Translation failed. Check the bot logs and try again.",
        )
        return

    token = secrets.token_urlsafe(16)
    with pending_translations_lock:
        pending_translations[token] = PendingTranslation(
            user_id=body["user_id"],
            channel_id=body["channel_id"],
            translated_text=translated_text,
            created_at=time.time(),
        )

    respond(
        response_type="ephemeral",
        text=f"English preview:\n{translated_text}",
        blocks=preview_blocks(translated_text, token),
    )


@app.command("/en2ru")
def handle_translate_to_russian(ack, body, respond):
    ack()
    text = (body.get("text") or "").strip()
    if not text:
        respond(
            response_type="ephemeral",
            text="Usage: /en2ru English text to translate",
        )
        return

    try:
        translated_text = translate(text, EN_TO_RU_SYSTEM_PROMPT)
    except Exception:
        logger.exception("Failed to translate /en2ru input")
        respond(
            response_type="ephemeral",
            text="Translation failed. Check the bot logs and try again.",
        )
        return

    respond(response_type="ephemeral", text=translated_text)


@app.action("tr_send")
def handle_send_translation(ack, body, client, respond):
    ack()
    cleanup_pending_translations()

    token = body["actions"][0]["value"]
    user_id = body.get("user", {}).get("id")

    with pending_translations_lock:
        pending = pending_translations.get(token)
        if pending and pending.user_id == user_id:
            pending_translations.pop(token, None)

    if not pending:
        respond(
            response_type="ephemeral",
            replace_original=True,
            text="This preview expired or was already handled.",
        )
        return

    if pending.user_id != user_id:
        respond(
            response_type="ephemeral",
            text="Only the person who created this preview can send it.",
        )
        return

    try:
        client.chat_postMessage(
            channel=pending.channel_id,
            text=pending.translated_text,
        )
    except Exception:
        with pending_translations_lock:
            pending_translations[token] = pending
        logger.exception("Failed to post translated message")
        respond(
            response_type="ephemeral",
            text="Could not post the message. Check the bot logs and try again.",
        )
        return

    respond(
        response_type="ephemeral",
        replace_original=True,
        text="Sent.",
    )


@app.action("tr_cancel")
def handle_cancel_translation(ack, body, respond):
    ack()
    cleanup_pending_translations()

    token = body["actions"][0]["value"]
    user_id = body.get("user", {}).get("id")

    with pending_translations_lock:
        pending = pending_translations.get(token)
        if pending and pending.user_id == user_id:
            pending_translations.pop(token, None)

    if pending and pending.user_id != user_id:
        respond(
            response_type="ephemeral",
            text="Only the person who created this preview can cancel it.",
        )
        return

    respond(
        response_type="ephemeral",
        replace_original=True,
        text="Canceled.",
    )


if __name__ == "__main__":
    logger.info("Starting slack-approval-translator in Socket Mode")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
