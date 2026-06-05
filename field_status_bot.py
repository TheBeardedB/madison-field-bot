import asyncio
import hashlib
import logging
import os
import re
import sys
import tempfile
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import aiohttp
import discord
import feedparser
import pytz
from PIL import Image, ImageDraw, ImageFont
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv

from db import Database
from llm_field_parser import GitHubModelsFieldParser

load_dotenv()

if os.name == "nt":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def _verbosity_depth() -> int:
    value = os.getenv("LOG_VERBOSITY", "vv").strip().lower()
    if value == "":
        return 0
    if value == "v":
        return 1
    if value == "vv":
        return 2
    if value == "vvv":
        return 3
    return 2

formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
console_handler = logging.StreamHandler(sys.stdout)
try:
    console_handler.stream.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass
console_handler.setFormatter(formatter)

root_logger = logging.getLogger()
root_logger.setLevel({0: logging.ERROR, 1: logging.WARNING, 2: logging.INFO, 3: logging.DEBUG}[_verbosity_depth()])
if not any(isinstance(handler, logging.StreamHandler) for handler in root_logger.handlers):
    root_logger.addHandler(console_handler)

logger = logging.getLogger(__name__)

DEFAULT_FEED_URL = "https://www.madisonal.gov/RSSFeed.aspx?ModID=1&CID=Field-Status-6"
NEWSFLASH_URL = "https://www.madisonal.gov/m/newsflash?cat=6"
POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "2"))
DEV_CHANNEL_ID = 1412578432629346314

FIELD_LAYOUT = {
    "Palmer": [1, 2, 3, 4, 5, 7, 8, 9, 10],
    "Dublin": list(range(1, 10)),
}

IMAGE_CARD_COLORS = {
    "open": "#2ecc71",
    "closed": "#e74c3c",
    "unknown": "#f1c40f",
}

IMAGE_THEME_COLORS = {
    "Palmer": "#2d7dd2",
    "Dublin": "#7d5ba6",
}

IMAGE_RENDER_VERSION = "2026-05-29-7"
LAST_PARSER_LLM = "llm"
LAST_PARSER_FALLBACK = "fallback"
LOW_CONFIDENCE_REASON = "Low_Confidence"
RETRY_LIMIT_REASON = "retry_limit_reached"
MAX_LLM_PARSE_ATTEMPTS = 3
PERMANENT_LLM_FAILURE_REASONS = {"disabled", "missing_token", "llm_unavailable"}
TRANSIENT_LLM_FAILURE_REASONS = {"http_error", "network_error", "request_error", "invalid_response"}


class FieldStatusBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents, help_command=None)

        self.CST = pytz.timezone("US/Central")
        self.channel_id = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
        self.guild_id = int(os.getenv("DISCORD_GUILD_ID", "0"))
        self.is_dev_mode = self.channel_id == DEV_CHANNEL_ID
        self.feed_url = os.getenv("RSS_FEED_URL", DEFAULT_FEED_URL).strip()
        self.db_url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or os.getenv("POSTGRESQL_URL") or ""
        self.db = Database(self.db_url)
        self.llm_parser = GitHubModelsFieldParser()
        self.feed_id = os.getenv("FEED_ID", "madison-field-status")
        self.feed_state: Dict[str, Optional[str]] = self._default_feed_state()
        self._poll_lock = asyncio.Lock()
        self._bootstrapped = False
        self._app_commands_synced = False

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _default_feed_state() -> Dict[str, Optional[str]]:
        return {
            "last_pub_date": None,
            "last_entry_key": None,
            "last_message_id": None,
            "last_status": None,
            "last_parser": None,
            "last_parse_reason": None,
            "last_parse_attempts": 0,
            "render_version": None,
        }

    async def _load_feed_state(self) -> Dict[str, Optional[str]]:
        state = self._default_feed_state()
        db_state = await self.db.get_feed_status(self.feed_id)
        if db_state:
            state.update({key: db_state.get(key) for key in state})
        return state

    async def _persist_feed_state(self) -> None:
        await self.db.set_feed_status(
            self.feed_id,
            last_pub_date=self.feed_state.get("last_pub_date"),
            last_entry_key=self.feed_state.get("last_entry_key"),
            last_message_id=self.feed_state.get("last_message_id"),
            last_status=self.feed_state.get("last_status"),
            last_parser=self.feed_state.get("last_parser"),
            last_parse_reason=self.feed_state.get("last_parse_reason"),
            last_parse_attempts=self.feed_state.get("last_parse_attempts"),
            render_version=self.feed_state.get("render_version"),
        )

    @staticmethod
    def _normalize_parse_reason(reason: Optional[str]) -> str:
        if not reason:
            return "unknown"

        normalized = reason.strip()
        if normalized.lower() == "low_confidence":
            return LOW_CONFIDENCE_REASON
        return normalized

    @staticmethod
    def _should_retry_llm(
        last_parser: Optional[str],
        last_parse_reason: Optional[str],
        last_parse_attempts: Optional[int],
    ) -> bool:
        parser = (last_parser or "").strip().lower()
        reason = (last_parse_reason or "").strip().lower()
        attempts = int(last_parse_attempts or 0)
        return (
            parser != LAST_PARSER_LLM
            and reason != "low_confidence"
            and reason not in PERMANENT_LLM_FAILURE_REASONS
            and attempts < MAX_LLM_PARSE_ATTEMPTS
        )

    @staticmethod
    def _skip_llm_reason(last_parse_reason: Optional[str], last_parse_attempts: Optional[int]) -> str:
        reason = (last_parse_reason or "").strip().lower()
        if reason == "low_confidence":
            return LOW_CONFIDENCE_REASON
        if reason in PERMANENT_LLM_FAILURE_REASONS:
            return reason
        if int(last_parse_attempts or 0) >= MAX_LLM_PARSE_ATTEMPTS:
            return RETRY_LIMIT_REASON
        return last_parse_reason or "unknown"

    # ------------------------------------------------------------------
    # Feed fetching and parsing
    # ------------------------------------------------------------------

    async def fetch_latest_entry(self) -> Optional[dict]:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; MadisonFieldBot/1.0)",
            "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        }
        timeout = aiohttp.ClientTimeout(total=20)

        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(self.feed_url) as response:
                    logger.info("Fetched RSS feed %s -> HTTP %s", self.feed_url, response.status)
                    if response.status != 200:
                        return None

                    content = await response.text()
                    feed = feedparser.parse(content)
                    if not feed.entries:
                        return None
                    return feed.entries[0]
        except Exception as exc:
            logger.error("Failed to fetch RSS feed: %s", exc, exc_info=True)
            return None

    @staticmethod
    def _clean_text(text: str) -> str:
        cleaned = unescape(text or "")
        cleaned = re.sub(r"<[^>]+>", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    @staticmethod
    def _entry_key(pub_date: str, title: str, link: str) -> str:
        raw = f"{pub_date}|{title}|{link}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _parse_field_numbers(self, chunk: str) -> List[int]:
        numbers: List[int] = []
        normalized = chunk.lower().replace(" and ", ",")
        for token in re.split(r",\s*", normalized):
            token = token.strip()
            if not token:
                continue

            range_match = re.fullmatch(r"(\d+)\s*(?:-|to)\s*(\d+)", token)
            if range_match:
                start = int(range_match.group(1))
                end = int(range_match.group(2))
                step = 1 if end >= start else -1
                numbers.extend(range(start, end + step, step))
                continue

            amp_match = re.fullmatch(r"(\d+)\s*&\s*(\d+)", token)
            if amp_match:
                numbers.extend([int(amp_match.group(1)), int(amp_match.group(2))])
                continue

            plain_numbers = re.findall(r"\d+", token)
            numbers.extend(int(value) for value in plain_numbers)

        return sorted(set(numbers))

    def _set_all_fields(self, statuses: Dict[str, Dict[int, str]], state: str) -> None:
        for park, field_numbers in FIELD_LAYOUT.items():
            for field_number in field_numbers:
                statuses[park][field_number] = state

    def _apply_park_wide_statements(self, content: str, statuses: Dict[str, Dict[int, str]]) -> None:
        text = content.lower()

        combined_all_closed_patterns = [
            r"\ball\s+fields\s+at\s+dublin\s+park\s+and\s+palmer\s+park\s+are\s+closed\b",
            r"\ball\s+fields\s+at\s+palmer\s+park\s+and\s+dublin\s+park\s+are\s+closed\b",
            r"\ball\s+fields\s+at\s+the\s+dublin\s+park\s+and\s+palmer\s+park\s+are\s+closed\b",
            r"\ball\s+fields\s+at\s+dublin\s+park\s+and\s+palmer\s+park\s+(?:will\s+)?(?:remain|stay|be|keep)\s+closed\b",
            r"\ball\s+fields\s+at\s+palmer\s+park\s+and\s+dublin\s+park\s+(?:will\s+)?(?:remain|stay|be|keep)\s+closed\b",
            r"\ball\s+fields\s+at\s+the\s+dublin\s+park\s+and\s+palmer\s+park\s+(?:will\s+)?(?:remain|stay|be|keep)\s+closed\b",
        ]
        combined_all_open_patterns = [
            r"\ball\s+fields\s+at\s+dublin\s+park\s+and\s+palmer\s+park\s+are\s+open\b",
            r"\ball\s+fields\s+at\s+palmer\s+park\s+and\s+dublin\s+park\s+are\s+open\b",
            r"\ball\s+fields\s+at\s+the\s+dublin\s+park\s+and\s+palmer\s+park\s+are\s+open\b",
            r"\ball\s+fields\s+at\s+dublin\s+park\s+and\s+palmer\s+park\s+(?:will\s+)?(?:remain|stay|be|keep)\s+open\b",
            r"\ball\s+fields\s+at\s+palmer\s+park\s+and\s+dublin\s+park\s+(?:will\s+)?(?:remain|stay|be|keep)\s+open\b",
            r"\ball\s+fields\s+at\s+the\s+dublin\s+park\s+and\s+palmer\s+park\s+(?:will\s+)?(?:remain|stay|be|keep)\s+open\b",
        ]

        if any(re.search(pattern, text) for pattern in combined_all_closed_patterns):
            self._set_all_fields(statuses, "closed")
            return

        if any(re.search(pattern, text) for pattern in combined_all_open_patterns):
            self._set_all_fields(statuses, "open")
            return

        park_patterns = {
            "Palmer": {
                "closed": [
                    r"\ball\s+fields\s+at\s+palmer\s+park\s+are\s+closed\b",
                    r"\ball\s+palmer\s+park\s+fields?\s+are\s+closed\b",
                    r"\ball\s+palmer\s+soccer\s+fields?\s+are\s+closed\b",
                    r"\ball\s+fields\s+at\s+palmer\s+park\s+(?:will\s+)?(?:remain|stay|be|keep)\s+closed\b",
                    r"\ball\s+palmer\s+park\s+fields?\s+(?:will\s+)?(?:remain|stay|be|keep)\s+closed\b",
                    r"\ball\s+palmer\s+soccer\s+fields?\s+(?:will\s+)?(?:remain|stay|be|keep)\s+closed\b",
                ],
                "open": [
                    r"\ball\s+fields\s+at\s+palmer\s+park\s+are\s+open\b",
                    r"\ball\s+palmer\s+park\s+fields?\s+are\s+open\b",
                    r"\ball\s+palmer\s+soccer\s+fields?\s+are\s+open\b",
                    r"\ball\s+fields\s+at\s+palmer\s+park\s+(?:will\s+)?(?:remain|stay|be|keep)\s+open\b",
                    r"\ball\s+palmer\s+park\s+fields?\s+(?:will\s+)?(?:remain|stay|be|keep)\s+open\b",
                    r"\ball\s+palmer\s+soccer\s+fields?\s+(?:will\s+)?(?:remain|stay|be|keep)\s+open\b",
                ],
            },
            "Dublin": {
                "closed": [
                    r"\ball\s+fields\s+at\s+dublin\s+park\s+are\s+closed\b",
                    r"\ball\s+dublin\s+park\s+fields?\s+are\s+closed\b",
                    r"\ball\s+dublin\s+soccer\s+fields?\s+are\s+closed\b",
                    r"\ball\s+fields\s+at\s+dublin\s+park\s+(?:will\s+)?(?:remain|stay|be|keep)\s+closed\b",
                    r"\ball\s+dublin\s+park\s+fields?\s+(?:will\s+)?(?:remain|stay|be|keep)\s+closed\b",
                    r"\ball\s+dublin\s+soccer\s+fields?\s+(?:will\s+)?(?:remain|stay|be|keep)\s+closed\b",
                ],
                "open": [
                    r"\ball\s+fields\s+at\s+dublin\s+park\s+are\s+open\b",
                    r"\ball\s+dublin\s+park\s+fields?\s+are\s+open\b",
                    r"\ball\s+dublin\s+soccer\s+fields?\s+are\s+open\b",
                    r"\ball\s+fields\s+at\s+dublin\s+park\s+(?:will\s+)?(?:remain|stay|be|keep)\s+open\b",
                    r"\ball\s+dublin\s+park\s+fields?\s+(?:will\s+)?(?:remain|stay|be|keep)\s+open\b",
                    r"\ball\s+dublin\s+soccer\s+fields?\s+(?:will\s+)?(?:remain|stay|be|keep)\s+open\b",
                ],
            },
        }

        if (
            re.search(r"\ball\s+fields\s+(?:are\s+)?closed\b", text)
            and "palmer" not in text
            and "dublin" not in text
        ):
            self._set_all_fields(statuses, "closed")
            return

        if (
            re.search(r"\ball\s+fields\s+(?:are\s+)?open\b", text)
            and "palmer" not in text
            and "dublin" not in text
            and "closed" not in text
        ):
            self._set_all_fields(statuses, "open")

        for park, patterns in park_patterns.items():
            if any(re.search(pattern, text) for pattern in patterns["closed"]):
                for field_number in FIELD_LAYOUT[park]:
                    statuses[park][field_number] = "closed"
            if any(re.search(pattern, text) for pattern in patterns["open"]):
                for field_number in FIELD_LAYOUT[park]:
                    statuses[park][field_number] = "open"

    def parse_field_statuses_with_metadata(
        self,
        title: str,
        content: str,
        allow_llm: bool = True,
        skipped_reason: Optional[str] = None,
    ) -> Tuple[Dict[str, Dict[int, str]], str, str, bool]:
        llm_attempted = False
        llm_statuses = None
        llm_reason = "unknown"

        if allow_llm:
            llm_statuses, llm_reason, llm_attempted = self._parse_field_statuses_with_llm(title, content)
        if llm_statuses is not None:
            return llm_statuses, LAST_PARSER_LLM, llm_reason, llm_attempted

        statuses = self._default_statuses()

        combined_text = " ".join(part for part in [title, content] if part).strip()
        if not combined_text:
            reason = self._normalize_parse_reason(skipped_reason if not allow_llm else llm_reason)
            return statuses, LAST_PARSER_FALLBACK, reason, llm_attempted

        text = combined_text.lower()
        self._apply_park_wide_statements(text, statuses)

        extension_pattern = re.compile(
            r"\b(?:palmer\s+)?extension(?:\s+fields?)?"
            r"(?:\s+(?:are|is|was|were|will|be|currently|still|for|today|this|the|all|remaining|remain|remains|stays|stay))*"
            r"\s*(?P<state>open|closed)\b"
        )

        for match in extension_pattern.finditer(text):
            state = match.group("state")
            for field_number in range(7, 11):
                statuses["Palmer"][field_number] = state

        field_pattern = re.compile(
            r"\b(?P<park>palmer|dublin)\s+soccer\s+"
            r"(?P<fields>\d+(?:\s*(?:-|to|&)\s*\d+)?(?:\s*,\s*\d+(?:\s*(?:-|to|&)\s*\d+)?)*)"
            r"(?:\s+(?:are|is|was|were|will|be|currently|still|for|today|this|the|all|remaining|remain|remains|stays|stay))*"
            r"\s*(?P<state>open|closed)\b"
        )

        for match in field_pattern.finditer(text):
            park = "Palmer" if match.group("park") == "palmer" else "Dublin"
            state = match.group("state")
            field_numbers = self._parse_field_numbers(match.group("fields"))
            for field_number in field_numbers:
                if field_number in statuses[park]:
                    statuses[park][field_number] = state

        reason = skipped_reason if not allow_llm else llm_reason
        return statuses, LAST_PARSER_FALLBACK, self._normalize_parse_reason(reason), llm_attempted

    def parse_field_statuses(self, title: str, content: str) -> Dict[str, Dict[int, str]]:
        statuses, _, _, _ = self.parse_field_statuses_with_metadata(title, content)
        return statuses

    @staticmethod
    def summarize_statuses(statuses: Dict[str, Dict[int, str]]) -> str:
        open_count = sum(
            1 for park_fields in statuses.values() for field_state in park_fields.values() if field_state == "open"
        )
        closed_count = sum(
            1 for park_fields in statuses.values() for field_state in park_fields.values() if field_state == "closed"
        )
        unknown_count = sum(
            1 for park_fields in statuses.values() for field_state in park_fields.values() if field_state == "unknown"
        )
        total_count = sum(len(park_fields) for park_fields in statuses.values())
        if open_count == total_count:
            return "open"
        if closed_count == total_count:
            return "closed"
        if unknown_count == total_count:
            return "unknown"
        return "partial"

    @staticmethod
    def format_field_state(state: str) -> str:
        if state == "closed":
            return "Closed"
        if state == "unknown":
            return "Unknown"
        return "Open"

    @staticmethod
    def _default_statuses() -> Dict[str, Dict[int, str]]:
        return {
            "Palmer": {field_number: "unknown" for field_number in FIELD_LAYOUT["Palmer"]},
            "Dublin": {field_number: "unknown" for field_number in FIELD_LAYOUT["Dublin"]},
        }

    @staticmethod
    def _apply_llm_statuses(statuses: Dict[str, Dict[int, str]], llm_result: Dict) -> Dict[str, Dict[int, str]]:
        parks = llm_result.get("parks", {})
        if not isinstance(parks, dict):
            return statuses

        for park_name, field_map in parks.items():
            if park_name not in statuses or not isinstance(field_map, dict):
                continue
            for field_key, state in field_map.items():
                try:
                    field_number = int(field_key)
                except (TypeError, ValueError):
                    match = re.search(r"(\d+)\s*$", str(field_key).strip())
                    if not match:
                        continue
                    try:
                        field_number = int(match.group(1))
                    except (TypeError, ValueError):
                        continue
                if field_number not in statuses[park_name]:
                    continue
                if state in {"open", "closed", "unknown"}:
                    statuses[park_name][field_number] = state
        return statuses

    def _parse_field_statuses_with_llm(
        self, title: str, content: str
    ) -> Tuple[Optional[Dict[str, Dict[int, str]]], str, bool]:
        if not self.llm_parser.is_ready():
            logger.info(
                "LLM field parser unavailable: enabled=%s token_present=%s",
                self.llm_parser.enabled,
                bool(self.llm_parser.token),
            )
            return None, "llm_unavailable", False

        llm_result = self.llm_parser.extract_field_statuses_with_llm(title, content, FIELD_LAYOUT)
        confidence = float(llm_result.get("confidence", 0.0) or 0.0)
        reason = llm_result.get("reason", "unknown")
        logger.info(
            "LLM field parser result reason=%s confidence=%.2f threshold=%.2f title_len=%s content_len=%s",
            reason,
            confidence,
            self.llm_parser.min_confidence,
            len(title or ""),
            len(content or ""),
        )
        if reason != "ok" or confidence < self.llm_parser.min_confidence:
            parse_reason = self._normalize_parse_reason(reason)
            logger.info(
                "LLM field parser skipped: reason=%s confidence=%.2f threshold=%.2f",
                parse_reason,
                confidence,
                self.llm_parser.min_confidence,
            )
            return None, parse_reason, True

        statuses = self._default_statuses()
        statuses = self._apply_llm_statuses(statuses, llm_result)
        logger.info(
            "LLM field parser accepted: confidence=%.2f summary=Palmer=%s Dublin=%s",
            confidence,
            statuses["Palmer"],
            statuses["Dublin"],
        )
        return statuses, "ok", True

    @staticmethod
    def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        candidates = []
        repo_dir = Path(__file__).resolve().parent
        pil_font_dir = Path(ImageFont.__file__).resolve().parent
        font_kind = "bold" if bold else "regular"
        roboto_root = repo_dir / "Roboto"
        roboto_static_dir = roboto_root / "static"
        logger.info(
            "Loading %s font at size %s from repo Roboto folder %s",
            font_kind,
            size,
            roboto_root,
        )
        if roboto_static_dir.exists():
            static_candidates = sorted(
                roboto_static_dir.glob("*.ttf"),
                key=lambda path: (
                    0 if bold and "bold" in path.name.lower() else 1 if bold else 0 if "regular" in path.name.lower() or "variable" in path.name.lower() else 1,
                    path.name.lower(),
                ),
            )
            candidates.extend(static_candidates)
            logger.debug(
                "Found %s Roboto static font candidate(s) in %s",
                len(static_candidates),
                roboto_static_dir,
            )
        candidates.extend(
            [
                roboto_root / "Roboto-VariableFont_wdth,wght.ttf",
                roboto_root / "Roboto-Regular.ttf",
                roboto_root / "Roboto-Bold.ttf",
            ]
        )
        if os.name == "nt":
            candidates.extend(
                [
                    Path(r"C:\Windows\Fonts\Roboto-Bold.ttf" if bold else r"C:\Windows\Fonts\Roboto-Regular.ttf"),
                    Path(r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf"),
                    Path(r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf"),
                ]
            )
        candidates.extend(
            [
                pil_font_dir / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
                pil_font_dir / "fonts" / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
                if bold
                else Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf")
                if bold
                else Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
                ]
            )

        seen = set()
        for path in candidates:
            path_str = str(path)
            if path_str in seen:
                continue
            seen.add(path_str)
            logger.debug("Checking font candidate: %s", path_str)
            if path and os.path.exists(path_str):
                try:
                    font = ImageFont.truetype(path_str, size=size)
                    font_name = None
                    try:
                        font_name = font.getname()
                    except Exception:
                        font_name = None
                    logger.info(
                        "Loaded %s font from %s%s",
                        font_kind,
                        path_str,
                        f" ({font_name[0]}, {font_name[1]})" if font_name else "",
                    )
                    return font
                except Exception:
                    logger.warning("Failed to load font candidate: %s", path_str, exc_info=True)
                    continue

        logger.warning("Falling back to Pillow default font for %s font at size %s", font_kind, size)
        return ImageFont.load_default()

    @staticmethod
    def _wrap_text(text: str, font: ImageFont.ImageFont, max_width: int) -> str:
        words = text.split()
        if not words:
            return text

        lines = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if font.getlength(candidate) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return "\n".join(lines)

    def _card_label_for_field(self, park: str, field_number: int) -> str:
        if park == "Palmer" and field_number in range(7, 11):
            return f"Field {field_number}\nExtension"
        return f"Field {field_number}"

    def build_park_image(self, park: str, field_states: Dict[int, str]) -> str:
        field_numbers = list(field_states.keys())
        total_fields = len(field_numbers)
        columns = 2 if total_fields >= 10 else 3
        rows = (total_fields + columns - 1) // columns

        width = 1280
        top_margin = 54
        left_margin = 60
        right_margin = 60
        bottom_margin = 54
        gap = 18
        card_width = (width - left_margin - right_margin - gap * (columns - 1)) // columns
        card_height = 190
        height = top_margin + bottom_margin + rows * card_height + (rows - 1) * gap

        background = Image.new("RGBA", (width, height), "#0f1218")
        draw = ImageDraw.Draw(background)

        field_font = self._load_font(64, bold=True)
        extension_font = self._load_font(28, bold=True)
        logger.debug(
            "%s renderer font metrics: field_font=%s extension_font=%s",
            park,
            getattr(field_font, "size", "unknown"),
            getattr(extension_font, "size", "unknown"),
        )
        card_fill = "#171b22"
        card_outline = "#2a313c"
        card_text = "#ffffff"
        extension_text = "#cbd5e1"
        indicator_size = 84
        indicator_spacing = 24

        for index, field_number in enumerate(field_numbers):
            row = index // columns
            column = index % columns
            x1 = left_margin + column * (card_width + gap)
            y1 = top_margin + row * (card_height + gap)
            x2 = x1 + card_width
            y2 = y1 + card_height

            state = field_states[field_number]
            dot_color = IMAGE_CARD_COLORS[state]
            draw.rounded_rectangle((x1, y1, x2, y2), radius=24, fill=card_fill, outline=card_outline, width=2)

            label = self._card_label_for_field(park, field_number)
            label_lines = label.split("\n")
            primary_label = label_lines[0]
            secondary_label = label_lines[1] if len(label_lines) > 1 else None

            primary_bbox = draw.textbbox((0, 0), primary_label, font=field_font)
            primary_width = primary_bbox[2] - primary_bbox[0]
            primary_height = primary_bbox[3] - primary_bbox[1]

            secondary_width = 0
            secondary_height = 0
            if secondary_label:
                secondary_bbox = draw.textbbox((0, 0), secondary_label, font=extension_font)
                secondary_width = secondary_bbox[2] - secondary_bbox[0]
                secondary_height = secondary_bbox[3] - secondary_bbox[1]

            text_width = max(primary_width, secondary_width)
            text_height = primary_height + (8 + secondary_height if secondary_label else 0)
            group_width = indicator_size + indicator_spacing + text_width
            group_height = max(indicator_size, text_height)

            group_x = x1 + (card_width - group_width) / 2
            group_y = y1 + (card_height - group_height) / 2

            indicator_y = group_y + (group_height - indicator_size) / 2
            indicator_box = (group_x, indicator_y, group_x + indicator_size, indicator_y + indicator_size)
            if state == "closed":
                cut = indicator_size * 0.28
                draw.polygon(
                    [
                        (indicator_box[0] + cut, indicator_box[1]),
                        (indicator_box[2] - cut, indicator_box[1]),
                        (indicator_box[2], indicator_box[1] + cut),
                        (indicator_box[2], indicator_box[3] - cut),
                        (indicator_box[2] - cut, indicator_box[3]),
                        (indicator_box[0] + cut, indicator_box[3]),
                        (indicator_box[0], indicator_box[3] - cut),
                        (indicator_box[0], indicator_box[1] + cut),
                    ],
                    fill=dot_color,
                )
            elif state == "unknown":
                draw.polygon(
                    [
                        (indicator_box[0] + indicator_size / 2, indicator_box[1]),
                        (indicator_box[2], indicator_box[3]),
                        (indicator_box[0], indicator_box[3]),
                    ],
                    fill=dot_color,
                )
            else:
                draw.ellipse(indicator_box, fill=dot_color)

            text_x = group_x + indicator_size + indicator_spacing
            text_y = group_y + (group_height - text_height) / 2
            draw.text((text_x, text_y), primary_label, font=field_font, fill=card_text)

            if secondary_label:
                draw.text(
                    (text_x, text_y + primary_height + 8),
                    secondary_label,
                    font=extension_font,
                    fill=extension_text,
                )

        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f"_{park.lower()}_fields.png")
        temp_file.close()
        background.convert("RGB").save(temp_file.name, format="PNG", optimize=True)
        return temp_file.name

    def build_embeds(
        self,
        entry: dict,
        content: str,
        statuses: Dict[str, Dict[int, str]],
        pub_date: str,
    ) -> tuple[List[discord.Embed], Dict[str, str]]:
        summary = self.summarize_statuses(statuses)
        title = entry.get("title") or "Field Status Update"
        link = entry.get("link") or ""

        if summary == "open":
            color = 0x2ECC71
        elif summary == "closed":
            color = 0xE74C3C
        elif summary == "unknown":
            color = 0xF1C40F
        else:
            color = 0xF39C12

        published_at = self._parse_datetime(pub_date)
        description = content or "No additional details were provided in the RSS entry."
        if len(description) > 4096:
            description = description[:4093] + "..."

        main_embed = discord.Embed(
            title=title[:256],
            url=NEWSFLASH_URL,
            description=description,
            color=color,
            timestamp=published_at,
        )
        main_embed.add_field(name="Overall Status", value=summary.title(), inline=True)
        main_embed.add_field(
            name="Updated",
            value=published_at.strftime("%b %d, %Y %I:%M %p %Z") if pub_date else "Unknown",
            inline=True,
        )
        main_embed.add_field(
            name="Source",
            value=f"[Open Madison Newsflash]({NEWSFLASH_URL})",
            inline=False,
        )
        footer_text = "Madison Parks & Recreation"
        if self.is_dev_mode:
            footer_text += " | Dev mode"
        main_embed.set_footer(text=footer_text)

        palmer_embed = discord.Embed(
            title="Palmer Fields",
            description=(
                f"{sum(1 for state in statuses['Palmer'].values() if state == 'closed')} closed, "
                f"{sum(1 for state in statuses['Palmer'].values() if state == 'unknown')} unknown of {len(FIELD_LAYOUT['Palmer'])}."
            ),
            color=0x2D7DD2 if summary == "open" else color,
        )
        palmer_image = self.build_park_image("Palmer", statuses["Palmer"])
        palmer_embed.set_image(url="attachment://palmer_fields.png")

        dublin_embed = discord.Embed(
            title="Dublin Fields",
            description=(
                f"{sum(1 for state in statuses['Dublin'].values() if state == 'closed')} closed, "
                f"{sum(1 for state in statuses['Dublin'].values() if state == 'unknown')} unknown of {len(FIELD_LAYOUT['Dublin'])}."
            ),
            color=0x7D5BA6 if summary == "open" else color,
        )
        dublin_image = self.build_park_image("Dublin", statuses["Dublin"])
        dublin_embed.set_image(url="attachment://dublin_fields.png")

        return [main_embed, palmer_embed, dublin_embed], {
            "palmer_fields.png": palmer_image,
            "dublin_fields.png": dublin_image,
        }

    def _parse_datetime(self, pub_date: str) -> datetime:
        if not pub_date:
            return datetime.now(self.CST)
        try:
            parsed = parsedate_to_datetime(pub_date)
            if parsed.tzinfo is None:
                return self.CST.localize(parsed)
            return parsed.astimezone(self.CST)
        except Exception:
            return datetime.now(self.CST)

    @staticmethod
    def _extract_entry_content(entry: dict) -> str:
        summary = entry.get("summary", "") or entry.get("description", "")
        if summary:
            return summary

        content = entry.get("content", [])
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict):
                return first.get("value", "") or ""
        return ""

    async def _append_history_entry(
        self,
        guild_id: int,
        entry: dict,
        content: str,
        pub_date: str,
        statuses: Dict[str, Dict[int, str]],
        entry_key: str,
    ) -> None:
        closed_fields = [
            f"{park} {field_number}"
            for park, field_map in statuses.items()
            for field_number, state in field_map.items()
            if state == "closed"
        ]

        await self.db.add_history_entry(
            guild_id,
            self.feed_id,
            {
                "pub_date": pub_date,
                "title": entry.get("title", ""),
                "content": content,
                "status": self.summarize_statuses(statuses),
                "entry_key": entry_key,
                "closed_fields": closed_fields,
                "contains_soccer": "soccer" in content.lower(),
            },
        )

    # ------------------------------------------------------------------
    # Discord message management
    # ------------------------------------------------------------------

    async def _resolve_channel(self) -> Optional[discord.TextChannel]:
        channel = self.get_channel(self.channel_id)
        if channel is not None:
            return channel

        try:
            fetched = await self.fetch_channel(self.channel_id)
            if isinstance(fetched, discord.TextChannel):
                return fetched
        except Exception as exc:
            logger.error("Failed to resolve target channel: %s", exc, exc_info=True)
        return None

    async def _cleanup_channel_messages(
        self, channel: discord.TextChannel, keep_message_id: Optional[int]
    ) -> None:
        if not self.user:
            return

        deleted_count = 0

        try:
            async for message in channel.history(limit=None, oldest_first=False):
                if keep_message_id and message.id == int(keep_message_id):
                    continue

                try:
                    await message.delete()
                    deleted_count += 1
                except discord.NotFound:
                    continue
                except discord.Forbidden:
                    logger.error(
                        "Missing permission to delete message %s in #%s.",
                        message.id,
                        channel.name,
                    )
                    return
                except Exception as exc:
                    logger.error(
                        "Failed to delete message %s in #%s: %s",
                        message.id,
                        channel.name,
                        exc,
                        exc_info=True,
                    )

        except discord.Forbidden:
            logger.error("Missing permission to read message history in #%s.", channel.name)
            return
        except Exception as exc:
            logger.error("Failed to clean up channel #%s: %s", channel.name, exc, exc_info=True)
            return

        if deleted_count:
            logger.info("Deleted %s message(s) from #%s.", deleted_count, channel.name)

    @staticmethod
    def _build_file_objects(image_payloads: Dict[str, str]) -> List[discord.File]:
        return [discord.File(path, filename=filename) for filename, path in image_payloads.items()]

    @staticmethod
    def _cleanup_temp_files(image_payloads: Dict[str, str]) -> None:
        for path in image_payloads.values():
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except Exception:
                continue

    async def _upsert_single_message(
        self,
        channel: discord.TextChannel,
        embeds: List[discord.Embed],
        image_payloads: Dict[str, str],
    ) -> Optional[int]:
        try:
            message = await channel.send(embeds=embeds, files=self._build_file_objects(image_payloads))
            return message.id
        except discord.Forbidden:
            logger.error("Missing permission to send the Discord message.")
        except Exception as exc:
            logger.error("Failed to send Discord message: %s", exc, exc_info=True)
            return None
        finally:
            self._cleanup_temp_files(image_payloads)

    async def sync_latest_entry(
        self,
        force_refresh: bool = False,
        channel: Optional[discord.TextChannel] = None,
        persist_state: bool = True,
        append_history: bool = True,
        cleanup_channel: bool = True,
    ) -> None:
        async with self._poll_lock:
            entry = await self.fetch_latest_entry()
            if not entry:
                logger.warning("No RSS entries were returned from the feed.")
                return

            title = entry.get("title", "Field Status Update")
            link = entry.get("link", "")
            pub_date = entry.get("published") or entry.get("updated") or ""
            content = self._clean_text(self._extract_entry_content(entry))
            entry_key = self._entry_key(pub_date, title, link)

            if channel is None:
                channel = await self._resolve_channel()
            if not channel:
                logger.error("Could not resolve the configured Discord channel.")
                return

            logger.info(
                "Processing %s update for channel %s%s",
                "development" if self.is_dev_mode else "production",
                channel.id,
                " (test mode)" if not persist_state else "",
            )

            last_parser = self.feed_state.get("last_parser")
            last_parse_reason = self.feed_state.get("last_parse_reason")
            last_parse_attempts = int(self.feed_state.get("last_parse_attempts") or 0)
            same_entry = entry_key == self.feed_state.get("last_entry_key")
            should_retry_llm = self._should_retry_llm(last_parser, last_parse_reason, last_parse_attempts)
            attempt_llm = force_refresh or not same_entry or should_retry_llm

            if (
                not force_refresh
                and same_entry
                and self.feed_state.get("last_message_id")
                and not attempt_llm
            ):
                try:
                    await channel.fetch_message(int(self.feed_state["last_message_id"]))
                    logger.info("Latest RSS entry is unchanged; no Discord edit is needed.")
                    return
                except discord.NotFound:
                    logger.info("Stored message was deleted; recreating the current status message.")
                except discord.Forbidden:
                    logger.error("Missing permission to inspect the stored Discord message.")
                    return
                except Exception as exc:
                    logger.error("Failed to verify the stored Discord message: %s", exc, exc_info=True)
                    return
            elif same_entry and not attempt_llm:
                logger.info(
                    "Latest RSS entry is unchanged and LLM retry is skipped after parser=%s reason=%s attempts=%s.",
                    last_parser or "unknown",
                    last_parse_reason or "unknown",
                    last_parse_attempts,
                )
            elif same_entry and should_retry_llm:
                logger.info(
                    "Latest RSS entry is unchanged, last parse used parser=%s reason=%s attempts=%s; retrying LLM.",
                    last_parser or "unknown",
                    last_parse_reason or "unknown",
                    last_parse_attempts,
                )

            skip_reason = self._skip_llm_reason(last_parse_reason, last_parse_attempts) if same_entry and not attempt_llm else None
            statuses, parser_name, parse_reason, llm_attempted = self.parse_field_statuses_with_metadata(
                title,
                content,
                allow_llm=attempt_llm,
                skipped_reason=skip_reason,
            )

            if cleanup_channel:
                await self._cleanup_channel_messages(channel, None)

            embeds, image_payloads = self.build_embeds(entry, content, statuses, pub_date)
            guild_id = channel.guild.id if channel.guild else 0

            message_id = await self._upsert_single_message(channel, embeds, image_payloads)
            if message_id is None:
                return

            parse_reason_key = (parse_reason or "").strip().lower()
            if parser_name == LAST_PARSER_LLM:
                parse_attempts = 0
            elif llm_attempted and parse_reason_key in TRANSIENT_LLM_FAILURE_REASONS:
                base_attempts = last_parse_attempts if same_entry else 0
                parse_attempts = min(base_attempts + 1, MAX_LLM_PARSE_ATTEMPTS)
            else:
                parse_attempts = last_parse_attempts if same_entry else 0

            if persist_state:
                self.feed_state["last_message_id"] = str(message_id)
                self.feed_state["last_pub_date"] = pub_date
                self.feed_state["last_entry_key"] = entry_key
                self.feed_state["last_status"] = self.summarize_statuses(statuses)
                self.feed_state["last_parser"] = parser_name
                self.feed_state["last_parse_reason"] = parse_reason
                self.feed_state["last_parse_attempts"] = parse_attempts
                self.feed_state["render_version"] = IMAGE_RENDER_VERSION
                await self._persist_feed_state()
                if append_history:
                    try:
                        await self._append_history_entry(
                            guild_id,
                            entry,
                            content,
                            pub_date,
                            statuses,
                            entry_key,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to append feed history entry; continuing with posted status message: %s",
                            exc,
                            exc_info=True,
                        )

            logger.info(
                "Posted refreshed Discord message with entry %s (%s)%s",
                title,
                pub_date or "no pub_date",
                " in test mode" if not persist_state else "",
            )

    # ------------------------------------------------------------------
    # Bot lifecycle
    # ------------------------------------------------------------------

    async def setup_hook(self) -> None:
        await self.db.connect()
        self.feed_state = await self._load_feed_state()

        @self.tree.command(name="test", description="Re-evaluate the latest field update and post it to a chosen channel.")
        @app_commands.describe(channel="Channel to post the test update to")
        async def test(interaction: discord.Interaction, channel: discord.TextChannel):
            await interaction.response.defer(ephemeral=True, thinking=True)
            logger.info(
                "/test invoked by %s for channel %s",
                interaction.user,
                channel.id,
            )
            try:
                await self.sync_latest_entry(
                    force_refresh=True,
                    channel=channel,
                    persist_state=False,
                    append_history=False,
                    cleanup_channel=False,
                )
                await interaction.followup.send(
                    f"Posted a fresh test update to {channel.mention}.",
                    ephemeral=True,
                )
            except Exception as exc:
                logger.error("Test command failed: %s", exc, exc_info=True)
                await interaction.followup.send(
                    f"Test update failed: {exc}",
                    ephemeral=True,
                )

    async def close(self):
        try:
            await self.db.close()
        finally:
            await super().close()

    async def _sync_app_commands(self) -> None:
        if self._app_commands_synced:
            return

        try:
            global_synced = await self.tree.sync()
            logger.info("Synced %s global app command(s).", len(global_synced))

            target_guild_id = self.guild_id
            if not target_guild_id:
                channel = await self._resolve_channel()
                if channel and channel.guild:
                    target_guild_id = channel.guild.id

            if not target_guild_id:
                logger.warning("Skipping guild app command sync because no guild could be resolved.")
                return

            synced = await self.tree.sync(guild=discord.Object(id=target_guild_id))
            self._app_commands_synced = True
            logger.info("Synced %s guild app command(s) to guild %s", len(synced), target_guild_id)
        except Exception as exc:
            logger.error("Failed to sync app commands: %s", exc, exc_info=True)

    async def on_ready(self):
        logger.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "unknown")
        if self._bootstrapped:
            return

        self._bootstrapped = True
        await self._sync_app_commands()

        needs_refresh = self.feed_state.get("render_version") != IMAGE_RENDER_VERSION
        if not needs_refresh and self.feed_state.get("last_message_id"):
            channel = await self._resolve_channel()
            if channel:
                try:
                    await channel.fetch_message(int(self.feed_state["last_message_id"]))
                except discord.NotFound:
                    needs_refresh = True
                except Exception as exc:
                    logger.warning("Could not verify the stored Discord message on startup: %s", exc, exc_info=True)

        if needs_refresh:
            await self.sync_latest_entry(force_refresh=True)

        if not self.poll_feed.is_running():
            self.poll_feed.start()

    @tasks.loop(minutes=POLL_INTERVAL_MINUTES)
    async def poll_feed(self):
        await self.sync_latest_entry()

    @poll_feed.before_loop
    async def before_poll_feed(self):
        await self.wait_until_ready()


def main():
    token = os.getenv("DISCORD_BOT_TOKEN")
    channel_id = os.getenv("DISCORD_CHANNEL_ID")

    if not token:
        logger.error("DISCORD_BOT_TOKEN environment variable not set")
        raise SystemExit(1)

    if not channel_id:
        logger.error("DISCORD_CHANNEL_ID environment variable not set")
        raise SystemExit(1)

    if not os.getenv("RSS_FEED_URL"):
        logger.info("RSS_FEED_URL not set; using the default Madison field feed.")

    db_url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or os.getenv("POSTGRESQL_URL")
    if not db_url:
        logger.error("DATABASE_URL environment variable not set")
        raise SystemExit(1)

    bot = FieldStatusBot()
    bot.run(token)


if __name__ == "__main__":
    main()
