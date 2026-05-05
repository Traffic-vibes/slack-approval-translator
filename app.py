import logging
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from threading import Lock
from typing import Dict, Optional, Tuple

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
AUTO_TRANSLATE_DB_PATH = os.getenv("DATABASE_PATH", "translator_bot.db")
PREVIEW_TTL_SECONDS = 15 * 60
AUTO_TRANSLATE_CHANNEL_TYPES = {"channel", "group", "im", "mpim"}
LATIN_RE = re.compile(r"[A-Za-z]")
CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")

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

SOFTER_EN_SYSTEM_PROMPT = f"""
You are an English writing assistant for an ad-tech and performance marketing team.
Rewrite the English Slack message to sound more polite, friendly, and natural for a workplace Slack conversation.
Preserve the exact meaning, facts, numbers, URLs, Slack mentions, line breaks, and simple formatting.
Do not add explanations, new commitments, or extra context.
Follow this ad-tech glossary:
{glossary_text()}
Return only the rewritten English text.
""".strip()

SHORTER_EN_SYSTEM_PROMPT = f"""
You are an English writing assistant for an ad-tech and performance marketing team.
Rewrite the English Slack message to be shorter while preserving the meaning.
Keep the tone professional and natural for a workplace Slack conversation.
Preserve facts, numbers, URLs, Slack mentions, line breaks, and simple formatting.
Do not add explanations, new commitments, or extra context.
Follow this ad-tech glossary:
{glossary_text()}
Return only the rewritten English text.
""".strip()


@dataclass
class PendingTranslation:
    user_id: str
    translated_text: str
    created_at: float


pending_translations: Dict[str, PendingTranslation] = {}
pending_translations_lock = Lock()
auto_translate_users_lock = Lock()

openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
app = App(token=os.environ["SLACK_BOT_TOKEN"])


def initialize_auto_translate_db() -> None:
    with auto_translate_users_lock:
        with sqlite3.connect(AUTO_TRANSLATE_DB_PATH) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS auto_translate_users (
                    user_id TEXT PRIMARY KEY,
                    enabled_at INTEGER NOT NULL
                )
                """
            )


def enable_auto_translate_for_user(user_id: str) -> None:
    with auto_translate_users_lock:
        with sqlite3.connect(AUTO_TRANSLATE_DB_PATH) as connection:
            connection.execute(
                """
                INSERT INTO auto_translate_users (user_id, enabled_at)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET enabled_at = excluded.enabled_at
                """,
                (user_id, int(time.time())),
            )


def disable_auto_translate_for_user(user_id: str) -> None:
    with auto_translate_users_lock:
        with sqlite3.connect(AUTO_TRANSLATE_DB_PATH) as connection:
            connection.execute(
                "DELETE FROM auto_translate_users WHERE user_id = ?",
                (user_id,),
            )


def is_auto_translate_enabled_for_user(user_id: str) -> bool:
    with auto_translate_users_lock:
        with sqlite3.connect(AUTO_TRANSLATE_DB_PATH) as connection:
            row = connection.execute(
                "SELECT 1 FROM auto_translate_users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
    return row is not None


def get_auto_translate_user_ids() -> list[str]:
    with auto_translate_users_lock:
        with sqlite3.connect(AUTO_TRANSLATE_DB_PATH) as connection:
            rows = connection.execute(
                "SELECT user_id FROM auto_translate_users ORDER BY user_id"
            ).fetchall()
    return [row[0] for row in rows]


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


def get_pending_for_action(
    token: str,
    user_id: Optional[str],
    action_verb: str,
) -> Tuple[Optional[PendingTranslation], Optional[str]]:
    with pending_translations_lock:
        pending = pending_translations.get(token)

    if not pending:
        return None, "This preview expired or was already handled."

    if pending.user_id != user_id:
        return (
            pending,
            f"Only the person who created this preview can {action_verb} it.",
        )

    return pending, None


def context_value(context, key: str) -> Optional[str]:
    if context is None:
        return None

    value = getattr(context, key, None)
    if value is not None:
        return value

    if hasattr(context, "get"):
        return context.get(key)

    return None


def is_message_from_this_app(event: dict, context) -> bool:
    bot_user_id = context_value(context, "bot_user_id")
    return bool(bot_user_id and event.get("user") == bot_user_id)


def count_words(text: str) -> int:
    return len(text.strip().split())


def log_auto_translate_message_event(event: dict) -> None:
    text = event.get("text") or ""
    logger.debug(
        "incoming message event: channel=%r channel_type=%r user=%r bot_id=%r "
        "subtype=%r text=%r ts=%r thread_ts=%r word_count=%d",
        event.get("channel"),
        event.get("channel_type"),
        event.get("user"),
        event.get("bot_id"),
        event.get("subtype"),
        text,
        event.get("ts"),
        event.get("thread_ts"),
        count_words(text),
    )


def log_auto_translate_skip(reason: str, event: dict) -> None:
    text = event.get("text") or ""
    logger.debug(
        "skipped: %s channel=%r channel_type=%r user=%r bot_id=%r subtype=%r "
        "ts=%r thread_ts=%r word_count=%d",
        reason,
        event.get("channel"),
        event.get("channel_type"),
        event.get("user"),
        event.get("bot_id"),
        event.get("subtype"),
        event.get("ts"),
        event.get("thread_ts"),
        count_words(text),
    )


def auto_translate_text_skip_reason(text: str) -> Optional[str]:
    stripped_text = text.strip()
    if not stripped_text:
        return "empty text"

    if stripped_text.startswith("/"):
        return "slash command"

    # Prevent noisy translations for short Slack replies.
    if count_words(stripped_text) <= 5:
        return "too short"

    has_latin = bool(LATIN_RE.search(stripped_text))
    has_cyrillic = bool(CYRILLIC_RE.search(stripped_text))
    if not (has_latin and not has_cyrillic):
        return "not english"

    return None


def should_auto_translate_text(text: str) -> bool:
    return auto_translate_text_skip_reason(text) is None


def auto_translation_message(translated_text: str) -> str:
    return f"Translation\n\n{translated_text}"


def auto_translation_thread_ts(event: dict) -> Optional[str]:
    return event.get("thread_ts") or event.get("ts")


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


def draft_preview_text(translated_text: str) -> str:
    return f"English preview:\n{translated_text}"


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
                    "text": {"type": "plain_text", "text": "Done"},
                    "style": "primary",
                    "action_id": "tr_done",
                    "value": token,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Softer"},
                    "action_id": "tr_softer",
                    "value": token,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Shorter"},
                    "action_id": "tr_shorter",
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


@app.event("message")
def handle_auto_translate_incoming_message(event, client, context):
    log_auto_translate_message_event(event)

    if event.get("channel_type") not in AUTO_TRANSLATE_CHANNEL_TYPES:
        log_auto_translate_skip("unsupported channel_type", event)
        return

    if (
        event.get("bot_id")
        or event.get("bot_profile")
        or event.get("subtype") == "bot_message"
    ):
        log_auto_translate_skip("bot message", event)
        return

    if event.get("subtype") is not None:
        log_auto_translate_skip("subtype", event)
        return

    if is_message_from_this_app(event, context):
        log_auto_translate_skip("own message", event)
        return

    text = (event.get("text") or "").strip()
    text_skip_reason = auto_translate_text_skip_reason(text)
    if text_skip_reason:
        log_auto_translate_skip(text_skip_reason, event)
        return

    channel_id = event.get("channel")
    if not channel_id:
        log_auto_translate_skip("missing channel", event)
        return

    recipient_user_ids = get_auto_translate_user_ids()
    if not recipient_user_ids:
        log_auto_translate_skip("no opted-in users", event)
        return

    message_user_id = event.get("user")
    if len(recipient_user_ids) == 1 and recipient_user_ids[0] == message_user_id:
        log_auto_translate_skip("own message", event)
        return

    try:
        translated_text = translate(text, EN_TO_RU_SYSTEM_PROMPT)
    except Exception:
        logger.exception("Failed to auto-translate incoming Slack message")
        log_auto_translate_skip("translation failed", event)
        return

    thread_ts = auto_translation_thread_ts(event)

    for user_id in recipient_user_ids:
        ephemeral_message = {
            "channel": channel_id,
            "user": user_id,
            "text": auto_translation_message(translated_text),
        }
        if thread_ts:
            ephemeral_message["thread_ts"] = thread_ts

        try:
            client.chat_postEphemeral(**ephemeral_message)
            logger.debug(
                "auto-translation sent: target_user_id=%r channel=%r thread_ts=%r",
                user_id,
                channel_id,
                thread_ts,
            )
        except Exception:
            logger.exception(
                "Failed to post auto-translation ephemeral message to user %s",
                user_id,
            )


@app.command("/tr_on")
def handle_auto_translate_on(ack, body, respond):
    ack()
    user_id = body.get("user_id")
    if not user_id:
        logger.error("Missing user_id in /tr_on payload")
        respond(
            response_type="ephemeral",
            text="Could not identify your Slack user.",
        )
        return

    try:
        enable_auto_translate_for_user(user_id)
    except Exception:
        logger.exception("Failed to enable auto-translation for user %s", user_id)
        respond(
            response_type="ephemeral",
            text="Could not update auto-translation status. Check the bot logs and try again.",
        )
        return

    respond(
        response_type="ephemeral",
        text="Auto-translation is now enabled for you.",
    )


@app.command("/tr_off")
def handle_auto_translate_off(ack, body, respond):
    ack()
    user_id = body.get("user_id")
    if not user_id:
        logger.error("Missing user_id in /tr_off payload")
        respond(
            response_type="ephemeral",
            text="Could not identify your Slack user.",
        )
        return

    try:
        disable_auto_translate_for_user(user_id)
    except Exception:
        logger.exception("Failed to disable auto-translation for user %s", user_id)
        respond(
            response_type="ephemeral",
            text="Could not update auto-translation status. Check the bot logs and try again.",
        )
        return

    respond(
        response_type="ephemeral",
        text="Auto-translation is now disabled for you.",
    )


@app.command("/tr_status")
def handle_auto_translate_status(ack, body, respond):
    ack()
    user_id = body.get("user_id")
    if not user_id:
        logger.error("Missing user_id in /tr_status payload")
        respond(
            response_type="ephemeral",
            text="Could not identify your Slack user.",
        )
        return

    try:
        is_enabled = is_auto_translate_enabled_for_user(user_id)
    except Exception:
        logger.exception("Failed to read auto-translation status for user %s", user_id)
        respond(
            response_type="ephemeral",
            text="Could not read auto-translation status. Check the bot logs and try again.",
        )
        return

    status = "enabled" if is_enabled else "disabled"
    respond(
        response_type="ephemeral",
        text=f"Auto-translation is currently {status} for you.",
    )


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
            translated_text=translated_text,
            created_at=time.time(),
        )

    respond(
        response_type="ephemeral",
        text=draft_preview_text(translated_text),
        blocks=preview_blocks(translated_text, token),
    )


@app.action("tr_done")
def handle_done_translation(ack, body, respond):
    ack()
    cleanup_pending_translations()

    token = body["actions"][0]["value"]
    user_id = body.get("user", {}).get("id")

    with pending_translations_lock:
        pending = pending_translations.get(token)
        if pending and pending.user_id == user_id:
            pending_translations.pop(token, None)

    if not pending:
        return

    if pending.user_id != user_id:
        return

    respond(
        replace_original=True,
        text="\u200b",
        blocks=[],
    )


def handle_rewrite_translation(ack, body, respond, system_prompt: str, action_name: str):
    ack()
    cleanup_pending_translations()

    token = body["actions"][0]["value"]
    user_id = body.get("user", {}).get("id")

    pending, error = get_pending_for_action(token, user_id, "rewrite")
    if error:
        if not pending:
            return

        respond(
            response_type="ephemeral",
            text=error,
        )
        return

    current_text = pending.translated_text

    try:
        rewritten_text = translate(current_text, system_prompt)
    except Exception:
        logger.exception("Failed to %s translation preview", action_name)
        return

    with pending_translations_lock:
        latest_pending = pending_translations.get(token)
        if latest_pending and latest_pending.user_id == user_id:
            latest_pending.translated_text = rewritten_text
        else:
            latest_pending = None

    if not latest_pending:
        return

    respond(
        response_type="ephemeral",
        replace_original=True,
        text=draft_preview_text(rewritten_text),
        blocks=preview_blocks(rewritten_text, token),
    )


@app.action("tr_softer")
def handle_softer_translation(ack, body, respond):
    handle_rewrite_translation(
        ack=ack,
        body=body,
        respond=respond,
        system_prompt=SOFTER_EN_SYSTEM_PROMPT,
        action_name="soften",
    )


@app.action("tr_shorter")
def handle_shorter_translation(ack, body, respond):
    handle_rewrite_translation(
        ack=ack,
        body=body,
        respond=respond,
        system_prompt=SHORTER_EN_SYSTEM_PROMPT,
        action_name="shorten",
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

    if not pending:
        return

    if pending.user_id != user_id:
        return

    respond(
        replace_original=True,
        text="\u200b",
        blocks=[],
    )


if __name__ == "__main__":
    initialize_auto_translate_db()
    logger.info("Starting slack-approval-translator in Socket Mode")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
