import os
import sys
import logging
import re
import json
import asyncio
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import discord
from discord.ext import tasks, commands
from discord import app_commands
import feedparser
import pytz
import aiohttp
from dotenv import load_dotenv

from db import Database

load_dotenv()

# On Windows, request UTF-8 console output
if os.name == 'nt':
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    try:
        import subprocess
        subprocess.run(['chcp', '65001'], shell=True, capture_output=True)
    except Exception:
        pass

formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

console_handler = logging.StreamHandler(sys.stdout)
try:
    console_handler.stream.reconfigure(encoding='utf-8')
except (AttributeError, OSError):
    pass
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(console_handler)

# Add a file handler when running locally (Railway provides ephemeral disk; not worth persisting)
if not os.getenv("RAILWAY_ENVIRONMENT"):
    try:
        file_handler = logging.FileHandler("field_status_bot.log", encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except OSError:
        pass

logger = logging.getLogger(__name__)


def safe_log(level, message):
    """Safely log a message, handling Unicode encoding issues"""
    try:
        if level == 'info':
            logger.info(message)
        elif level == 'error':
            logger.error(message)
        elif level == 'warning':
            logger.warning(message)
        elif level == 'debug':
            logger.debug(message)
    except UnicodeEncodeError:
        # Fallback: remove or replace problematic characters
        safe_message = message.encode('ascii', errors='replace').decode('ascii')
        if level == 'info':
            logger.info(f"[Unicode Error - Original message contained special characters] {safe_message}")
        elif level == 'error':
            logger.error(f"[Unicode Error - Original message contained special characters] {safe_message}")
        elif level == 'warning':
            logger.warning(f"[Unicode Error - Original message contained special characters] {safe_message}")
        elif level == 'debug':
            logger.debug(f"[Unicode Error - Original message contained special characters] {safe_message}")


class FieldStatusBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="/", intents=intents)

        self.CST = pytz.timezone("US/Central")
        self.CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
        self.CONFIG_FILE = "bot_config.json"

        # Populated in setup_hook from DB (Railway) or JSON file (local)
        self.config: Dict = {"guilds": {}}
        # {'{guild_id}:{feed_id}': last_pub_date} — in-memory cache, backed by DB
        self._feed_last_pub_dates: Dict[str, str] = {}
        # Database handle; None when running without DATABASE_URL
        self.db: Optional[Database] = None

        self.STATUS_COLORS = {
            "open": 0x00FF00,
            "partial": 0xFF8C00,
            "closed": 0xFF0000,
        }

    # ── Config helpers ────────────────────────────────────────────────────────

    DEFAULT_GUILD_CONFIG = {
        "feeds": {},
        "role_pings": {},
        "global_settings": {"timezone": "US/Central", "default_embed_color": 0x3498DB},
    }

    DEFAULT_FEED_CONFIG = {
        "name": "RSS Feed",
        "url": "",
        "channel_id": None,
        "enabled": True,
        "check_intervals": {"normal": 20, "peak": 5, "frequent": 1, "weather": 5},
        "schedule": {
            "peak_times": [
                {"start": "14:30", "end": "15:30", "days": [0, 1, 2, 3, 4]},
                {"start": "07:30", "end": "08:30", "days": [5, 6]},
            ],
            "weather_check": False,
            "weather_location": "",
        },
        "processing": {
            "content_parser": "generic",
            "custom_parser_function": None,
            "filters": [],
            "status_colors": {
                "default": 0x3498DB,
                "success": 0x00FF00,
                "warning": 0xFF8C00,
                "error": 0xFF0000,
            },
        },
        "embed_template": {
            "title_template": "{title}",
            "description_template": "{content}",
            "footer_text": "RSS Feed Update",
            "thumbnail_url": None,
            "fields": [],
        },
        "history": {"enabled": True, "max_entries": 100},
    }

    def _apply_config_defaults(self, config: Dict) -> Dict:
        """Fill in any missing keys on a loaded config dict."""
        for guild_id, guild_config in config.get("guilds", {}).items():
            for k, v in self.DEFAULT_GUILD_CONFIG.items():
                if k not in guild_config:
                    guild_config[k] = v
            for feed_id, feed_config in guild_config.get("feeds", {}).items():
                for k, v in self.DEFAULT_FEED_CONFIG.items():
                    if k not in feed_config:
                        feed_config[k] = v
        return config

    def _load_config_from_json(self) -> Dict:
        """Load config from local JSON file (used when no DATABASE_URL)."""
        try:
            if os.path.exists(self.CONFIG_FILE):
                with open(self.CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
                return self._apply_config_defaults(config)
        except Exception as e:
            logger.error(f"Error loading config from JSON: {e}")
        return {"guilds": {}}

    def _save_config_to_json(self):
        """Write config to local JSON file."""
        try:
            with open(self.CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving config to JSON: {e}")

    def save_config(self):
        """Persist config — DB when available, JSON file otherwise."""
        if self.db:
            for guild_id, guild_config in self.config["guilds"].items():
                asyncio.create_task(self.db.save_guild_config(guild_id, guild_config))
        else:
            self._save_config_to_json()
    
    def get_guild_config(self, guild_id: int) -> Dict:
        """Return (and lazily create) the in-memory config for a guild."""
        guild_id_str = str(guild_id)
        if guild_id_str not in self.config["guilds"]:
            import copy
            self.config["guilds"][guild_id_str] = copy.deepcopy(self.DEFAULT_GUILD_CONFIG)
            self.save_config()
        return self.config["guilds"][guild_id_str]

    def get_feed_config(self, guild_id: int, feed_id: str) -> Optional[Dict]:
        return self.get_guild_config(guild_id)["feeds"].get(feed_id)


    def parse_field_status(self, content: str) -> Tuple[str, List[str], bool]:
        """
        Parse field status from RSS content
        Returns: (overall_status, closed_fields, contains_soccer)
        """
        content_lower = content.lower()
        closed_fields = []
        contains_soccer = "soccer" in content_lower

        # Check for "all fields are open" first
        if any(
            phrase in content_lower
            for phrase in ["all fields are open", "all fields open"]
        ):
            return "open", [], contains_soccer

        # Check for complete closure (all fields in the city)
        complete_closure_patterns = [
            "all fields are closed",
            "all fields closed",
            "all parks are closed",
            "all parks closed",
        ]

        if any(phrase in content_lower for phrase in complete_closure_patterns):
            return "closed", ["All Fields"], contains_soccer

        # Check for weather-related complete closures
        weather_closure_patterns = [
            "due to lightning",
            "threat of severe weather",
            "due to weather",
        ]

        if any(phrase in content_lower for phrase in weather_closure_patterns):
            if "all fields" in content_lower and "closed" in content_lower:
                return "closed", ["All Fields"], contains_soccer

        # Extract specific field names that are mentioned as closed
        field_patterns = [
            # Specific field patterns
            r"(palmer soccer \d+(?:&\d+)?(?:-\d+)?)",
            r"(palmer soccer \d+)",
            r"(dublin soccer \d+)",
            r"(palmer baseball \d*)",
            r"(palmer softball)",
            r"(westco field \d+)",
            r"(westco fields \d+ & \d+)",
            r"(westco \d+)",
            r"(wellness center fields)",
            r"(dublin fields)",
            # Park-wide patterns
            r"all fields at (palmer park)",
            r"all fields at (dublin park)",
            r"all (dublin park) fields",
            r"(palmer park)",
            r"(dublin park)",
        ]

        for pattern in field_patterns:
            matches = re.finditer(pattern, content_lower)
            for match in matches:
                field_name = match.group(1)
                # Clean up the field name
                if field_name:
                    # Convert to title case and clean up
                    field_name = " ".join(
                        word.capitalize() for word in field_name.split()
                    )
                    if field_name not in closed_fields:
                        closed_fields.append(field_name)

        # More precise parsing for field closures - only look for clear patterns
        # Remove date prefixes first to avoid capturing them
        clean_content = re.sub(r"^\d+\.\d+\.\d+\s*-\s*", "", content_lower)
        clean_content = re.sub(r"\d+\.\d+\.\d+\s*update:\s*", "", clean_content)

        # Look for comma-separated list before "closed"
        list_closure_pattern = r"^([^.]+?)\s+(?:all\s+)?closed\s*[.;]"
        list_match = re.search(list_closure_pattern, clean_content)

        if list_match:
            field_list_text = list_match.group(1).strip()

            # Skip if it mentions "all fields" or similar
            if not any(
                skip in field_list_text
                for skip in ["all field", "all park", "due to", "because"]
            ):
                # Split by commas and clean up each field
                field_parts = re.split(r",\s*(?:and\s+)?", field_list_text)

                for part in field_parts:
                    part = part.strip()
                    # Skip if too short or has common false matches
                    if len(part) < 3 or any(
                        skip in part for skip in ["will be", "are", "for", "at", "on"]
                    ):
                        continue

                    # Clean up field name
                    field_name = " ".join(word.capitalize() for word in part.split())
                    if field_name not in closed_fields and len(field_name) < 30:
                        closed_fields.append(field_name)

        # Also look for "Field - CLOSED" pattern specifically
        dash_closed_pattern = r"([a-zA-Z\s\d&]+?)\s*-\s*closed"
        dash_matches = re.finditer(dash_closed_pattern, clean_content)

        for match in dash_matches:
            field_name = match.group(1).strip()

            # Clean up and validate
            if 3 < len(field_name) < 30:
                # Skip common false matches
                if not any(
                    skip in field_name.lower()
                    for skip in ["update", "other field", "all field"]
                ):
                    field_name = " ".join(
                        word.capitalize() for word in field_name.split()
                    )
                    if field_name not in closed_fields:
                        closed_fields.append(field_name)

        # Clean up and deduplicate closed_fields
        if closed_fields:
            # Remove duplicates while preserving order
            unique_fields = []
            for field in closed_fields:
                if field not in unique_fields:
                    unique_fields.append(field)

            # Filter out park names when specific fields from that park are mentioned
            filtered_fields = []
            for field in unique_fields:
                field_lower = field.lower()
                # Skip park-wide entries if we have specific fields from that park
                if "palmer park" in field_lower:
                    # Check if we have specific Palmer fields
                    has_palmer_specifics = any(
                        "palmer" in f.lower() and "park" not in f.lower()
                        for f in unique_fields
                    )
                    if not has_palmer_specifics:
                        filtered_fields.append(field)
                elif "dublin park" in field_lower:
                    # Check if we have specific Dublin fields
                    has_dublin_specifics = any(
                        "dublin" in f.lower() and "park" not in f.lower()
                        for f in unique_fields
                    )
                    if not has_dublin_specifics:
                        filtered_fields.append(field)
                else:
                    filtered_fields.append(field)

            closed_fields = filtered_fields

        # If we found specific closed fields, it's partial
        if closed_fields:
            return "partial", closed_fields, contains_soccer

        # Default to open if no closure indicators
        return "open", [], contains_soccer

    def extract_expected_update_time(self, content: str) -> Optional[datetime]:
        """Extract expected update time from content like 'Further updates at 4pm'"""
        patterns = [
            r"(?:further )?updates? (?:at |by |around )?(\d{1,2})(?::(\d{2}))?\s*([ap]m)",
            r"next update (?:at |by |around )?(\d{1,2})(?::(\d{2}))?\s*([ap]m)",
            r"check back (?:at |by |around )?(\d{1,2})(?::(\d{2}))?\s*([ap]m)",
        ]

        for pattern in patterns:
            match = re.search(pattern, content.lower())
            if match:
                hour = int(match.group(1))
                minute = int(match.group(2) or 0)
                ampm = match.group(3)

                if ampm == "pm" and hour != 12:
                    hour += 12
                elif ampm == "am" and hour == 12:
                    hour = 0

                # Create datetime for today with extracted time
                now = datetime.now(self.CST)
                update_time = now.replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                )

                # If time is in the past, assume tomorrow
                if update_time < now:
                    update_time += timedelta(days=1)

                return update_time

        return None

    def should_post_update(
        self,
        status: str,
        closed_fields: List[str],
        contains_soccer: bool,
        previous_status: Optional[str],
    ) -> bool:
        """Determine if an update should be posted based on criteria"""

        # Fields have reopened after being closed
        if previous_status in ["closed", "partial"] and status == "open":
            return True

        # Any time soccer fields are closed
        if contains_soccer and any(
            "soccer" in field.lower() for field in closed_fields
        ):
            return True

        # All fields are closed
        if status == "closed":
            return True

        return False

    def categorize_fields_by_location(self, closed_fields: List[str]) -> dict:
        """Categorize closed fields by location (Dublin, Palmer, Other)"""
        dublin_fields = []
        palmer_fields = []
        other_fields = []
        
        for field in closed_fields:
            field_lower = field.lower()
            if "dublin" in field_lower:
                dublin_fields.append(field)
            elif "palmer" in field_lower:
                palmer_fields.append(field)
            else:
                other_fields.append(field)
        
        return {
            "dublin": dublin_fields,
            "palmer": palmer_fields,
            "other": other_fields
        }

    def create_status_embed(
        self, status: str, closed_fields: List[str], content: str, timestamp: datetime
    ) -> discord.Embed:
        """Create Discord embed for field status update"""

        # Determine title and color
        if status == "open":
            title = "🟢 Fields Open"
            color = self.STATUS_COLORS["open"]
        elif status == "closed":
            title = "🔴 All Fields Closed"
            color = self.STATUS_COLORS["closed"]
        else:  # partial
            title = "🟡 Some Fields Closed"
            color = self.STATUS_COLORS["partial"]

        embed = discord.Embed(title=title, color=color, timestamp=timestamp)

        # Add field information
        if status == "partial" and closed_fields:
            # Categorize fields by location for partial closures
            field_categories = self.categorize_fields_by_location(closed_fields)
            
            # Dublin section
            dublin_value = "Open" if not field_categories["dublin"] else "\n".join([f"• {field}" for field in field_categories["dublin"]])
            embed.add_field(name="Dublin", value=dublin_value, inline=True)
            
            # Palmer section  
            palmer_value = "Open" if not field_categories["palmer"] else "\n".join([f"• {field}" for field in field_categories["palmer"]])
            embed.add_field(name="Palmer", value=palmer_value, inline=True)
            
            # Other section
            other_value = "Open" if not field_categories["other"] else "\n".join([f"• {field}" for field in field_categories["other"]])
            embed.add_field(name="Other", value=other_value, inline=True)
            
        elif closed_fields:
            # For non-partial status, use the original format
            embed.add_field(
                name="Closed Fields",
                value="\n".join([f"• {field}" for field in closed_fields]),
                inline=False,
            )

        # Add original content (truncated if too long)
        content_preview = content[:500] + "..." if len(content) > 500 else content
        embed.add_field(name="Details", value=content_preview, inline=False)

        embed.set_footer(text="Madison Parks & Recreation")

        return embed

    async def send_status_update(
        self, channel: discord.TextChannel, embed: discord.Embed, role_pings: dict = None
    ):
        """Send status update to channel with optional role ping"""
        if role_pings is None:
            # Look up from guild config
            guild_config = self.get_guild_config(channel.guild.id)
            role_pings = guild_config.get("role_pings", {})

        channel_id_str = str(channel.id)
        role_ids = role_pings.get(channel_id_str, [])

        message_content = None
        role_mentions = []

        if role_ids:
            for role_id in role_ids:
                try:
                    role = channel.guild.get_role(int(role_id))
                    if role:
                        role_mentions.append(role.mention)
                        logger.info(f"Pinging role {role.name} in channel {channel.name}")
                    else:
                        logger.warning(
                            f"Role {role_id} not found in guild {channel.guild.name}"
                        )
                except Exception as e:
                    logger.error(f"Error getting role {role_id}: {e}")

            if role_mentions:
                embed_title = embed.title if embed.title else "Field Status Update"
                message_content = f"{embed_title}\n\n" + " ".join(role_mentions)

        await channel.send(content=message_content, embed=embed)

    async def check_weather_conditions(self) -> bool:
        """Check if rain or storms are forecast for Madison, AL today"""
        try:
            # Using a free weather API (you may need to get an API key)
            # For now, this is a placeholder - you'll need to implement actual weather checking
            # You could use OpenWeatherMap, WeatherAPI, or similar service
            
            # Placeholder logic - return False for now
            # In a real implementation, you would:
            # 1. Make API call to weather service for Madison, AL
            # 2. Check today's forecast for rain/storms
            # 3. Return True if rain/storms are forecast
            
            return False
        except Exception as e:
            logger.error(f"Error checking weather conditions: {e}")
            return False

    @tasks.loop(minutes=1)  # Check every minute to dynamically adjust interval
    async def check_rss_feeds(self):
        """Main RSS checking task - checks all feeds across all guilds"""
        if not hasattr(self, "_feed_last_checks"):
            self._feed_last_checks = {}

        current_time = datetime.now(self.CST)
        
        # Iterate through all guilds and their feeds
        for guild_id_str, guild_config in self.config["guilds"].items():
            try:
                guild_id = int(guild_id_str)
                guild = self.get_guild(guild_id)
                
                if not guild:
                    logger.warning(f"Guild {guild_id} not found, skipping feeds")
                    continue
                
                for feed_id, feed_config in guild_config["feeds"].items():
                    if not feed_config["enabled"]:
                        continue
                    
                    # Check if it's time to check this feed
                    feed_key = f"{guild_id}:{feed_id}"
                    last_check = self._feed_last_checks.get(feed_key, current_time - timedelta(minutes=30))
                    
                    # Determine interval for this specific feed
                    interval = await self.determine_feed_check_interval(guild_id, feed_id)
                    time_since_check = (current_time - last_check).total_seconds() / 60
                    
                    if time_since_check >= interval:
                        logger.info(f"Checking RSS feed '{feed_config['name']}' in guild {guild.name} (interval: {interval} minutes)")
                        self._feed_last_checks[feed_key] = current_time
                        
                        # Process the feed
                        await self.process_rss_feed(guild_id, feed_id, feed_config)
                        
            except Exception as e:
                logger.error(f"Error processing feeds for guild {guild_id_str}: {e}")

    async def determine_feed_check_interval(self, guild_id: int, feed_id: str) -> int:
        """Determine the check interval for a specific feed"""
        feed_config = self.get_feed_config(guild_id, feed_id)
        if not feed_config:
            return 20  # Default interval
            
        intervals = feed_config["check_intervals"]
        schedule = feed_config["schedule"]
        
        # Get timezone for this guild
        guild_config = self.get_guild_config(guild_id)
        timezone_str = guild_config["global_settings"]["timezone"]
        tz = pytz.timezone(timezone_str)
        now = datetime.now(tz)
        
        # Check if we have a specific expected update time (stored per feed)
        feed_key = f"{guild_id}:{feed_id}"
        if hasattr(self, '_feed_expected_updates') and feed_key in self._feed_expected_updates:
            next_expected = self._feed_expected_updates[feed_key]
            time_until_update = (next_expected - now).total_seconds() / 60
            
            # Clear expired expected updates (more than 2 hours past)
            if time_until_update < -120:
                logger.info(f"Expected update for {feed_id} is more than 2 hours past, clearing")
                del self._feed_expected_updates[feed_key]
            else:
                # Within 10 minutes either side of expected update: frequent interval
                if -10 <= time_until_update <= 10:
                    return intervals["frequent"]
                # 30 minutes before to 2 hours after expected update: peak interval  
                elif -120 <= time_until_update <= 30:
                    return intervals["peak"]
        
        # Check weather conditions if enabled
        if schedule["weather_check"]:
            try:
                weather_check = await self.check_weather_conditions()
                if weather_check:
                    return intervals["weather"]
            except Exception as e:
                logger.error(f"Error checking weather for feed {feed_id}: {e}")
        
        # Check peak times defined in schedule
        for peak_time in schedule["peak_times"]:
            if now.weekday() in peak_time["days"]:
                start_time = datetime.strptime(peak_time["start"], "%H:%M").time()
                end_time = datetime.strptime(peak_time["end"], "%H:%M").time()
                current_time = now.time()
                
                if start_time <= current_time <= end_time:
                    return intervals["peak"]
        
        return intervals["normal"]

    async def process_rss_feed(self, guild_id: int, feed_id: str, feed_config: dict):
        """Process a single RSS feed."""
        try:
            feed = await self.fetch_rss_feed_url(feed_config["url"])
            if not feed or not feed.entries:
                logger.warning(f"No RSS entries found for feed {feed_id}")
                return

            latest_entry = feed.entries[0]
            content = latest_entry.get("summary", "") or latest_entry.get("description", "")
            pub_date = latest_entry.get("published", "")
            title = latest_entry.get("title", "RSS Update")

            # ── Dedup check ───────────────────────────────────────────────────
            feed_key = f"{guild_id}:{feed_id}"
            guild_id_str = str(guild_id)

            if self.db:
                # Fast pre-check against in-memory cache — avoids a DB round-trip
                # on the common case where nothing has changed.
                if self._feed_last_pub_dates.get(feed_key) == pub_date:
                    logger.debug(f"No new update for {feed_id} (cache hit)")
                    return

                # Atomically claim this pub_date in the DB.
                # If another instance already claimed it (zero-downtime deploy overlap),
                # claim_pub_date returns False and we skip posting entirely.
                is_new = await self.db.claim_pub_date(guild_id_str, feed_id, pub_date)
                if not is_new:
                    logger.debug(f"pub_date {pub_date!r} already claimed for {feed_id} — skipping")
                    self._feed_last_pub_dates[feed_key] = pub_date
                    return

                self._feed_last_pub_dates[feed_key] = pub_date
            else:
                if self._feed_last_pub_dates.get(feed_key) == pub_date:
                    logger.debug(f"No new update for {feed_id} (pub_date unchanged)")
                    return
                self._feed_last_pub_dates[feed_key] = pub_date

            logger.info(f"New RSS update for {feed_id}: {pub_date!r}")

            # ── Parse & determine previous status for change detection ────────
            previous_status = await self.db.get_last_status(guild_id_str, feed_id) if self.db else None

            parsed_data = await self.parse_feed_content(
                feed_config, content, title, latest_entry, previous_status
            )

            # ── Post to Discord ───────────────────────────────────────────────
            if parsed_data.get("should_post", True):
                embed = await self.create_feed_embed(feed_config, parsed_data, pub_date)
                channel = self.get_channel(feed_config["channel_id"])
                if channel:
                    guild_config = self.get_guild_config(guild_id)
                    await self.send_feed_update(channel, embed, guild_config["role_pings"])
                    logger.info(f"Posted update for {feed_id} to #{channel.name}")
                else:
                    logger.error(f"Channel {feed_config['channel_id']} not found for {feed_id}")

            # ── Save history ──────────────────────────────────────────────────
            if self.db and feed_config["history"]["enabled"]:
                await self.db.add_history_entry(guild_id_str, feed_id, {
                    "pub_date": pub_date,
                    "title": title,
                    "content": content[:2000],
                    "status": parsed_data.get("status"),
                    "closed_fields": parsed_data.get("closed_fields", []),
                    "contains_soccer": parsed_data.get("contains_soccer", False),
                })

        except Exception as e:
            logger.error(f"Error processing RSS feed {feed_id}: {e}")

    async def fetch_rss_feed_url(self, url: str):
        """Fetch RSS feed from a specific URL"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        content = await response.text()
                        return feedparser.parse(content)
                    else:
                        logger.error(f"Failed to fetch RSS feed from {url}: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"Error fetching RSS feed from {url}: {e}")
            return None

    async def parse_feed_content(
        self,
        feed_config: dict,
        content: str,
        title: str,
        entry: dict,
        previous_status: Optional[str] = None,
    ) -> dict:
        """Parse feed content and decide whether to post."""
        parser_type = feed_config["processing"]["content_parser"]

        parsed_data = {
            "title": title,
            "content": content,
            "status": "default",
            "color": feed_config["processing"]["status_colors"]["default"],
            "should_post": True,
        }

        if parser_type == "field_status":
            status, closed_fields, contains_soccer = self.parse_field_status(content)
            parsed_data.update({
                "status": status,
                "closed_fields": closed_fields,
                "contains_soccer": contains_soccer,
                "color": self.STATUS_COLORS.get(status, parsed_data["color"]),
                "should_post": self.should_post_update(
                    status, closed_fields, contains_soccer, previous_status
                ),
            })

            expected_update = self.extract_expected_update_time(content)
            if expected_update:
                parsed_data["expected_update"] = expected_update

        elif parser_type == "generic":
            parsed_data.update({
                "status": "update",
                "color": feed_config["processing"]["status_colors"]["default"],
                "should_post": True,
            })

        return parsed_data

    async def create_feed_embed(self, feed_config: dict, parsed_data: dict, pub_date: str) -> discord.Embed:
        """Create Discord embed for feed update"""
        template = feed_config["embed_template"]
        
        # Build safe format context (only string/number values, no dicts/lists)
        format_ctx = {
            "title": parsed_data["title"],
            "status": parsed_data.get("status", ""),
            "content": parsed_data["content"][:1000] + ("..." if len(parsed_data["content"]) > 1000 else ""),
        }

        title = template["title_template"].format(**format_ctx)
        description = template["description_template"].format(**format_ctx)
        
        embed = discord.Embed(
            title=title,
            description=description,
            color=parsed_data["color"],
            timestamp=datetime.now()
        )
        
        # Add custom fields from template
        for field in template["fields"]:
            embed.add_field(
                name=field["name"],
                value=field["value"].format(**format_ctx),
                inline=field.get("inline", False)
            )
        
        # Add special fields for field_status parser
        if feed_config["processing"]["content_parser"] == "field_status":
            if parsed_data.get("status") == "partial" and parsed_data.get("closed_fields"):
                field_categories = self.categorize_fields_by_location(parsed_data["closed_fields"])
                
                # Dublin section
                dublin_value = "Open" if not field_categories["dublin"] else "\n".join([f"• {field}" for field in field_categories["dublin"]])
                embed.add_field(name="Dublin", value=dublin_value, inline=True)
                
                # Palmer section  
                palmer_value = "Open" if not field_categories["palmer"] else "\n".join([f"• {field}" for field in field_categories["palmer"]])
                embed.add_field(name="Palmer", value=palmer_value, inline=True)
                
                # Other section
                other_value = "Open" if not field_categories["other"] else "\n".join([f"• {field}" for field in field_categories["other"]])
                embed.add_field(name="Other", value=other_value, inline=True)
                
            elif parsed_data.get("closed_fields"):
                embed.add_field(
                    name="Closed Fields",
                    value="\n".join([f"• {field}" for field in parsed_data["closed_fields"]]),
                    inline=False
                )
        
        # Set footer
        embed.set_footer(text=template["footer_text"])
        
        # Set thumbnail if configured
        if template["thumbnail_url"]:
            embed.set_thumbnail(url=template["thumbnail_url"])
            
        return embed

    async def send_feed_update(self, channel: discord.TextChannel, embed: discord.Embed, role_pings: dict):
        """Send feed update to channel with optional role pings"""
        channel_id_str = str(channel.id)
        role_ids = role_pings.get(channel_id_str, [])

        message_content = None
        role_mentions = []
        
        if role_ids:
            for role_id in role_ids:
                try:
                    role = channel.guild.get_role(int(role_id))
                    if role:
                        role_mentions.append(role.mention)
                        logger.info(f"Pinging role {role.name} in channel {channel.name}")
                    else:
                        logger.warning(f"Role {role_id} not found in guild {channel.guild.name}")
                except Exception as e:
                    logger.error(f"Error getting role {role_id}: {e}")
            
            if role_mentions:
                # Include embed title in message for notifications
                embed_title = embed.title if embed.title else "RSS Feed Update"
                message_content = f"{embed_title}\n\n" + " ".join(role_mentions)

        await channel.send(content=message_content, embed=embed)

    @check_rss_feeds.before_loop
    async def before_check_rss_feeds(self):
        """Wait for bot to be ready before starting the loop"""
        await self.wait_until_ready()
        logger.info("Starting field status monitoring")

    async def setup_hook(self):
        """Connect to DB (if configured), load persistent state, register commands."""
        db_url = os.getenv("DATABASE_URL")
        if db_url:
            try:
                self.db = Database(db_url)
                await self.db.connect()
                # Load all guild configs from DB into memory
                stored = await self.db.load_all_guild_configs()
                for guild_id, guild_config in stored.items():
                    self.config["guilds"][guild_id] = guild_config
                self._apply_config_defaults(self.config)
                # Pre-warm the pub-date cache so the first check loop is instant
                self._feed_last_pub_dates = await self.db.load_all_feed_states()
                logger.info(
                    f"Loaded {len(stored)} guild config(s) and "
                    f"{len(self._feed_last_pub_dates)} feed state(s) from DB"
                )
            except Exception as e:
                logger.error(f"Failed to connect to database: {e} — falling back to JSON")
                self.db = None
                self.config = self._load_config_from_json()
        else:
            logger.info("No DATABASE_URL set — using local JSON files")
            self.config = self._load_config_from_json()

        # Create set_role_ping command
        set_role_ping = app_commands.Command(
            name="set_role_ping",
            description="Add/remove role pings for field updates (toggles role, no role = clear all)",
            callback=self.set_role_ping_callback
        )
        set_role_ping.default_permissions = discord.Permissions(administrator=True)
        
        # Create repost_status command
        repost_status = app_commands.Command(
            name="repost_status",
            description="Repost a historical status update to a channel for testing",
            callback=self.repost_status_callback
        )
        repost_status.default_permissions = discord.Permissions(manage_messages=True)
        
        # Create check_permissions command
        check_permissions = app_commands.Command(
            name="check_permissions",
            description="Check bot permissions for field status monitoring",
            callback=self.check_permissions_callback
        )
        check_permissions.default_permissions = discord.Permissions(manage_messages=True)
        
        # Create field_history command
        field_history = app_commands.Command(
            name="field_history",
            description="Show recent field status history",
            callback=self.field_history_callback
        )
        field_history.default_permissions = discord.Permissions(manage_messages=True)
        
        # Create simple commands without parameters
        list_role_pings = app_commands.Command(
            name="list_role_pings",
            description="List all configured role pings for field updates",
            callback=self.list_role_pings_callback
        )
        list_role_pings.default_permissions = discord.Permissions(manage_messages=True)
        
        field_help = app_commands.Command(
            name="field_help",
            description="Show all available field status bot commands",
            callback=self.field_help_callback
        )
        
        # RSS Feed Management Commands
        add_feed = app_commands.Command(
            name="add_feed",
            description="Add a new RSS feed to monitor",
            callback=self.add_feed_callback
        )
        add_feed.default_permissions = discord.Permissions(administrator=True)
        
        list_feeds = app_commands.Command(
            name="list_feeds", 
            description="List all RSS feeds configured for this server",
            callback=self.list_feeds_callback
        )
        list_feeds.default_permissions = discord.Permissions(manage_messages=True)
        
        remove_feed = app_commands.Command(
            name="remove_feed",
            description="Remove an RSS feed",
            callback=self.remove_feed_callback
        )
        remove_feed.default_permissions = discord.Permissions(administrator=True)
        
        toggle_feed = app_commands.Command(
            name="toggle_feed",
            description="Enable or disable an RSS feed",
            callback=self.toggle_feed_callback
        )
        toggle_feed.default_permissions = discord.Permissions(administrator=True)
        
        feed_config = app_commands.Command(
            name="feed_config",
            description="View or modify RSS feed configuration",
            callback=self.feed_config_callback
        )
        feed_config.default_permissions = discord.Permissions(administrator=True)
        
        # Add all commands to tree
        self.tree.add_command(set_role_ping)
        self.tree.add_command(repost_status)
        self.tree.add_command(list_role_pings)
        self.tree.add_command(check_permissions)
        self.tree.add_command(field_help)
        self.tree.add_command(field_history)
        self.tree.add_command(add_feed)
        self.tree.add_command(list_feeds)
        self.tree.add_command(remove_feed)
        self.tree.add_command(toggle_feed)
        self.tree.add_command(feed_config)
        
        logger.info("Added all slash commands to command tree")

    # === COMMAND CALLBACKS ===
    
    async def set_role_ping_callback(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        role: discord.Role = None,
    ):
        """Add or remove role ping for a channel"""
        guild_id = interaction.guild_id
        guild_config = self.get_guild_config(guild_id)
        channel_id_str = str(channel.id)

        if role is None:
            # Remove all role pings for this channel
            if channel_id_str in guild_config["role_pings"] and guild_config["role_pings"][channel_id_str]:
                del guild_config["role_pings"][channel_id_str]
                self.save_config()
                await interaction.response.send_message(
                    f"✅ Removed all role pings for {channel.mention}", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"❌ No role pings configured for {channel.mention}", ephemeral=True
                )
        else:
            # Add or remove specific role
            if channel_id_str not in guild_config["role_pings"]:
                guild_config["role_pings"][channel_id_str] = []
            
            role_id_str = str(role.id)
            role_list = guild_config["role_pings"][channel_id_str]
            
            if role_id_str in role_list:
                # Remove the role
                role_list.remove(role_id_str)
                if not role_list:
                    # If no roles left, remove the channel entry
                    del guild_config["role_pings"][channel_id_str]
                self.save_config()
                await interaction.response.send_message(
                    f"✅ Removed {role.mention} from pings for {channel.mention}",
                    ephemeral=True,
                )
            else:
                # Add the role
                role_list.append(role_id_str)
                self.save_config()
                await interaction.response.send_message(
                    f"✅ Added {role.mention} to be pinged for updates in {channel.mention}",
                    ephemeral=True,
                )

    @app_commands.choices(status_type=[
        app_commands.Choice(name="Open", value="open"),
        app_commands.Choice(name="Partially Open", value="partial"),
        app_commands.Choice(name="Closed", value="closed"),
    ])
    async def repost_status_callback(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        status_type: str,
    ):
        """Repost a historical status update to a channel for testing"""
        # Collect all matching entries
        matching_entries = [
            entry for entry in self.status_history 
            if entry.get("status") == status_type
        ]

        if not matching_entries:
            await interaction.response.send_message(
                f"❌ No historical '{status_type}' status found", ephemeral=True
            )
            return

        # Randomly select one matching entry
        matching_entry = random.choice(matching_entries)

        # Create embed from historical data
        try:
            timestamp = datetime.fromisoformat(
                matching_entry["timestamp"].replace("Z", "+00:00")
            )
            embed = self.create_status_embed(
                matching_entry["status"],
                matching_entry["closed_fields"],
                matching_entry["content"],
                timestamp,
            )

            # Add footer note that this is historical
            embed.add_field(
                name="📋 Note",
                value=f"This is a repost of historical data from {timestamp.strftime('%Y-%m-%d %H:%M:%S CST')}",
                inline=False,
            )

            await self.send_status_update(channel, embed)
            await interaction.response.send_message(
                f"✅ Reposted '{status_type}' status to {channel.mention}",
                ephemeral=True,
            )

        except Exception as e:
            logger.error(f"Error reposting status: {e}")
            await interaction.response.send_message(
                f"❌ Error reposting status: {e}", ephemeral=True
            )

    async def list_role_pings_callback(self, interaction: discord.Interaction):
        """List all configured role pings"""
        guild_id = interaction.guild_id
        guild_config = self.get_guild_config(guild_id)
        
        if not guild_config["role_pings"]:
            await interaction.response.send_message(
                "No role pings configured", ephemeral=True
            )
            return

        embed = discord.Embed(title="Configured Role Pings", color=0x3498DB)

        for channel_id_str, role_id_list in guild_config["role_pings"].items():
            try:
                channel = self.get_channel(int(channel_id_str))
                channel_name = (
                    channel.mention
                    if channel
                    else f"Unknown Channel ({channel_id_str})"
                )
                
                role_names = []
                for role_id_str in role_id_list:
                    role = channel.guild.get_role(int(role_id_str)) if channel else None
                    role_name = role.mention if role else f"Unknown Role ({role_id_str})"
                    role_names.append(role_name)

                roles_text = ", ".join(role_names) if role_names else "No roles configured"
                embed.add_field(
                    name=f"Channel: {channel_name}",
                    value=f"Roles: {roles_text}",
                    inline=False,
                )
            except Exception as e:
                embed.add_field(
                    name=f"Channel ID: {channel_id_str}",
                    value=f"Role IDs: {', '.join(role_id_list)} (Error: {e})",
                    inline=False,
                )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def check_permissions_callback(
        self, interaction: discord.Interaction, channel: discord.TextChannel = None
    ):
        """Check bot permissions in the specified channel"""
        target_channel = channel or self.get_channel(self.CHANNEL_ID)

        if not target_channel:
            await interaction.response.send_message(
                f"❌ Cannot find channel. Configured channel ID: {self.CHANNEL_ID}\n"
                "Make sure the channel ID is correct and the bot is in the same server.",
                ephemeral=True,
            )
            return

        # Check bot permissions
        bot_member = target_channel.guild.get_member(self.user.id)
        if not bot_member:
            await interaction.response.send_message(
                f"❌ Bot is not a member of the server containing {target_channel.mention}",
                ephemeral=True,
            )
            return

        permissions = target_channel.permissions_for(bot_member)

        embed = discord.Embed(
            title=f"Bot Permissions Check: {target_channel.name}", color=0x3498DB
        )

        # Required permissions for field monitoring
        required_perms = {
            "View Channel": permissions.view_channel,
            "Send Messages": permissions.send_messages,
            "Embed Links": permissions.embed_links,
            "Read Message History": permissions.read_message_history,
        }

        # Optional permissions
        optional_perms = {
            "Mention Everyone": permissions.mention_everyone,  # For role pings
            "Use Slash Commands": permissions.use_slash_commands,
        }

        # Check required permissions
        required_status = ""
        all_required = True
        for perm_name, has_perm in required_perms.items():
            status = "✅" if has_perm else "❌"
            required_status += f"{status} {perm_name}\n"
            if not has_perm:
                all_required = False

        embed.add_field(
            name="Required Permissions", value=required_status, inline=False
        )

        # Check optional permissions
        optional_status = ""
        for perm_name, has_perm in optional_perms.items():
            status = "✅" if has_perm else "⚠️"
            optional_status += f"{status} {perm_name}\n"

        embed.add_field(
            name="Optional Permissions", value=optional_status, inline=False
        )

        # Overall status
        if all_required:
            embed.add_field(
                name="🎉 Status",
                value="All required permissions are present! Bot should work correctly.",
                inline=False,
            )
            embed.color = 0x00FF00  # Green
        else:
            embed.add_field(
                name="⚠️ Status",
                value="Missing required permissions! Bot will not work properly.\n"
                "Check the DISCORD_PERMISSIONS_GUIDE.md for help.",
                inline=False,
            )
            embed.color = 0xFF0000  # Red

        embed.add_field(
            name="Channel Info",
            value=f"Channel: {target_channel.mention}\n"
            f"Server: {target_channel.guild.name}\n"
            f"Channel ID: {target_channel.id}",
            inline=False,
        )

        # Add role information
        role_info = "Bot Roles:\n"
        for role in bot_member.roles[1:]:  # Skip @everyone
            role_info += f"• {role.name}\n"

        if len(role_info) > 1024:  # Discord embed field limit
            role_info = role_info[:1020] + "..."

        embed.add_field(
            name="Role Information", value=role_info or "No special roles", inline=False
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def field_help_callback(self, interaction: discord.Interaction):
        """Show all available field bot commands"""
        embed = discord.Embed(
            title="Madison Field Status Bot Commands",
            description="Monitor and manage field status updates",
            color=0x3498DB,
        )

        embed.add_field(
            name="📋 Monitoring Commands",
            value="◦ `/field_history [limit]` - Show recent status changes\n"
            "◦ `/repost_status` - Repost historical status for testing\n"
            "◦ `/check_permissions` - Check bot permissions in channels",
            inline=False,
        )

        embed.add_field(
            name="🔔 Role Ping Management",
            value="◦ `/set_role_ping` - Set or remove role ping for updates\n"
            "◦ `/list_role_pings` - List all configured role pings",
            inline=False,
        )

        embed.add_field(
            name="ℹ️ Info",
            value="◦ `/field_help` - Show this help message",
            inline=False,
        )

        embed.add_field(
            name="🌟 Auto-Monitoring",
            value="The bot automatically posts updates when:\n"
            "• Fields reopen after being closed\n"
            "• Soccer fields are closed\n"
            "• All fields are closed",
            inline=False,
        )

        embed.set_footer(text="Use slash commands for all bot interactions!")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def field_history_callback(
        self, interaction: discord.Interaction, limit: int = 5, feed_id: str = "madison_field_status"
    ):
        """Show recent field status history from the database."""
        if limit < 1 or limit > 20:
            await interaction.response.send_message(
                "❌ Limit must be between 1 and 20", ephemeral=True
            )
            return

        if not self.db:
            await interaction.response.send_message(
                "❌ History requires a database connection (set `DATABASE_URL`).", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        guild_id_str = str(interaction.guild_id)
        entries = await self.db.get_history(guild_id_str, feed_id, limit)

        if not entries:
            await interaction.followup.send("No history found for this feed.", ephemeral=True)
            return

        STATUS_EMOJI = {"open": "🟢", "partial": "🟡", "closed": "🔴"}
        embed = discord.Embed(
            title=f"Field Status History — last {len(entries)} updates",
            color=0x3498DB,
        )

        for entry in entries:
            status = entry.get("status", "unknown")
            emoji = STATUS_EMOJI.get(status, "⚪")
            closed = entry.get("closed_fields") or []
            soccer = " ⚽" if entry.get("contains_soccer") else ""
            ts = entry.get("detected_at", "")[:19].replace("T", " ")

            field_info = f"\nClosed: {', '.join(closed)}" if closed else ""
            embed.add_field(
                name=f"{emoji} {status.title()}{soccer}",
                value=f"{ts} UTC{field_info}",
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    async def on_ready(self):
        """Called when bot is ready"""
        logger.info(f"Bot logged in as {self.user.name} ({self.user.id})")

        # Auto-configure the Madison field status feed from env vars for all guilds
        rss_url = os.getenv("RSS_FEED_URL", "https://www.madisonal.gov/RSSFeed.aspx?ModID=1&CID=Field-Status-6")
        channel_id = self.CHANNEL_ID

        if channel_id:
            for guild in self.guilds:
                guild_config = self.get_guild_config(guild.id)
                if "madison_field_status" not in guild_config["feeds"]:
                    logger.info(f"Auto-configuring Madison field status feed for guild {guild.name}")
                    guild_config["feeds"]["madison_field_status"] = {
                        "name": "Madison Field Status",
                        "url": rss_url,
                        "channel_id": channel_id,
                        "enabled": True,
                        "check_intervals": {
                            "normal": 20,
                            "peak": 5,
                            "frequent": 1,
                            "weather": 5
                        },
                        "schedule": {
                            "peak_times": [
                                {"start": "14:30", "end": "15:30", "days": [0, 1, 2, 3, 4]},
                                {"start": "07:30", "end": "08:30", "days": [5, 6]}
                            ],
                            "weather_check": False,
                            "weather_location": ""
                        },
                        "processing": {
                            "content_parser": "field_status",
                            "custom_parser_function": None,
                            "filters": [],
                            "status_colors": {
                                "default": 0x3498DB,
                                "success": 0x00FF00,
                                "warning": 0xFF8C00,
                                "error": 0xFF0000
                            }
                        },
                        "embed_template": {
                            "title_template": "{title}",
                            "description_template": "{content}",
                            "footer_text": "Madison Parks & Recreation",
                            "thumbnail_url": None,
                            "fields": []
                        },
                        "history": {
                            "enabled": True,
                            "max_entries": 100,
                            "file_path": None
                        }
                    }
                    self.save_config()

        # Sync slash commands
        try:
            logger.info("Syncing slash commands...")
            synced = await self.tree.sync()
            logger.info(f"Successfully synced {len(synced)} slash commands globally")
            for command in synced:
                logger.info(f"Synced command: /{command.name}")
        except discord.HTTPException as e:
            logger.error(f"HTTP error syncing slash commands: {e}")
            if e.status == 429:
                logger.error("Rate limited - commands may have been synced recently")
            elif e.status == 403:
                logger.error("Bot lacks permissions to sync commands")
            else:
                logger.error(f"HTTP {e.status}: {e.text}")
        except Exception as e:
            logger.error(f"Failed to sync slash commands: {e}")
            logger.error("Commands may not be available until next bot restart")

        if not self.check_rss_feeds.is_running():
            self.check_rss_feeds.start()

    async def on_command_error(self, ctx, error):
        """Handle legacy command errors (if any remain)"""
        logger.error(f"Legacy command error: {error}")

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        """Handle application command errors"""
        if isinstance(error, app_commands.CommandSignatureMismatch):
            logger.error(
                f"Command signature mismatch for {interaction.command.name}: {error}"
            )
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Command signature mismatch. The bot may need to resync commands.",
                    ephemeral=True,
                )
        elif isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ You don't have permission to use this command", ephemeral=True
            )
        elif isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"❌ Command is on cooldown. Try again in {error.retry_after:.1f}s",
                ephemeral=True,
            )
        else:
            logger.error(f"App command error: {error}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"❌ An error occurred: {error}", ephemeral=True
                )
            else:
                await interaction.followup.send(
                    f"❌ An error occurred: {error}", ephemeral=True
                )

    # === RSS FEED MANAGEMENT CALLBACKS ===
    
    @app_commands.choices(parser_type=[
        app_commands.Choice(name="Generic RSS", value="generic"),
        app_commands.Choice(name="Field Status", value="field_status"),
        app_commands.Choice(name="Custom", value="custom")
    ])
    async def add_feed_callback(
        self,
        interaction: discord.Interaction,
        name: str,
        url: str,
        channel: discord.TextChannel,
        parser_type: str = "generic"
    ):
        """Add a new RSS feed to monitor"""
        guild_id = interaction.guild_id
        guild_config = self.get_guild_config(guild_id)
        
        # Generate a unique feed ID
        feed_id = name.lower().replace(" ", "_").replace("-", "_")
        counter = 1
        original_feed_id = feed_id
        while feed_id in guild_config["feeds"]:
            feed_id = f"{original_feed_id}_{counter}"
            counter += 1
        
        # Create default feed config
        default_feed_config = {
            "name": name,
            "url": url,
            "channel_id": channel.id,
            "enabled": True,
            "check_intervals": {
                "normal": 20,
                "peak": 5,
                "frequent": 1,
                "weather": 5
            },
            "schedule": {
                "peak_times": [],
                "weather_check": False,
                "weather_location": ""
            },
            "processing": {
                "content_parser": parser_type,
                "custom_parser_function": None,
                "filters": [],
                "status_colors": {
                    "default": 0x3498DB,
                    "success": 0x00FF00,
                    "warning": 0xFF8C00,
                    "error": 0xFF0000
                }
            },
            "embed_template": {
                "title_template": "{title}",
                "description_template": "{content}",
                "footer_text": "RSS Feed Update",
                "thumbnail_url": None,
                "fields": []
            },
            "history": {
                "enabled": True,
                "max_entries": 100,
                "file_path": f"feed_history_{guild_id}_{feed_id}.json"
            }
        }
        
        # Add the feed to the guild config
        guild_config["feeds"][feed_id] = default_feed_config
        self.save_config()
        
        embed = discord.Embed(
            title="✅ RSS Feed Added",
            color=0x00FF00,
            description=f"Successfully added RSS feed **{name}**"
        )
        embed.add_field(name="Feed ID", value=feed_id, inline=True)
        embed.add_field(name="URL", value=url, inline=False)
        embed.add_field(name="Channel", value=channel.mention, inline=True)
        embed.add_field(name="Parser", value=parser_type, inline=True)
        embed.add_field(name="Status", value="Enabled", inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def list_feeds_callback(self, interaction: discord.Interaction):
        """List all RSS feeds configured for this server"""
        guild_id = interaction.guild_id
        guild_config = self.get_guild_config(guild_id)
        
        if not guild_config["feeds"]:
            await interaction.response.send_message(
                "❌ No RSS feeds configured for this server.", ephemeral=True
            )
            return
        
        embed = discord.Embed(
            title="📡 RSS Feeds",
            color=0x3498DB,
            description=f"Configured feeds for **{interaction.guild.name}**"
        )
        
        for feed_id, feed_config in guild_config["feeds"].items():
            status = "✅ Enabled" if feed_config["enabled"] else "❌ Disabled"
            channel = self.get_channel(feed_config["channel_id"])
            channel_name = channel.mention if channel else "Unknown Channel"
            
            embed.add_field(
                name=f"{feed_config['name']} ({feed_id})",
                value=f"**URL:** {feed_config['url'][:50]}...\n**Channel:** {channel_name}\n**Status:** {status}\n**Parser:** {feed_config['processing']['content_parser']}",
                inline=False
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def remove_feed_callback(self, interaction: discord.Interaction, feed_id: str):
        """Remove an RSS feed"""
        guild_id = interaction.guild_id
        guild_config = self.get_guild_config(guild_id)
        
        if feed_id not in guild_config["feeds"]:
            await interaction.response.send_message(
                f"❌ Feed `{feed_id}` not found. Use `/list_feeds` to see available feeds.", 
                ephemeral=True
            )
            return
        
        feed_name = guild_config["feeds"][feed_id]["name"]
        del guild_config["feeds"][feed_id]
        self.save_config()
        
        await interaction.response.send_message(
            f"✅ Successfully removed RSS feed **{feed_name}** (`{feed_id}`)", 
            ephemeral=True
        )

    async def toggle_feed_callback(self, interaction: discord.Interaction, feed_id: str):
        """Enable or disable an RSS feed"""
        guild_id = interaction.guild_id
        guild_config = self.get_guild_config(guild_id)
        
        if feed_id not in guild_config["feeds"]:
            await interaction.response.send_message(
                f"❌ Feed `{feed_id}` not found. Use `/list_feeds` to see available feeds.", 
                ephemeral=True
            )
            return
        
        feed_config = guild_config["feeds"][feed_id]
        feed_config["enabled"] = not feed_config["enabled"]
        self.save_config()
        
        status = "enabled" if feed_config["enabled"] else "disabled"
        emoji = "✅" if feed_config["enabled"] else "❌"
        
        await interaction.response.send_message(
            f"{emoji} RSS feed **{feed_config['name']}** (`{feed_id}`) has been {status}", 
            ephemeral=True
        )

    async def feed_config_callback(self, interaction: discord.Interaction, feed_id: str):
        """View RSS feed configuration"""
        guild_id = interaction.guild_id
        guild_config = self.get_guild_config(guild_id)
        
        if feed_id not in guild_config["feeds"]:
            await interaction.response.send_message(
                f"❌ Feed `{feed_id}` not found. Use `/list_feeds` to see available feeds.", 
                ephemeral=True
            )
            return
        
        feed_config = guild_config["feeds"][feed_id]
        channel = self.get_channel(feed_config["channel_id"])
        
        embed = discord.Embed(
            title=f"⚙️ Feed Configuration: {feed_config['name']}",
            color=0x3498DB
        )
        
        embed.add_field(name="Feed ID", value=feed_id, inline=True)
        embed.add_field(name="Status", value="✅ Enabled" if feed_config["enabled"] else "❌ Disabled", inline=True)
        embed.add_field(name="Channel", value=channel.mention if channel else "Unknown", inline=True)
        
        embed.add_field(name="RSS URL", value=feed_config["url"], inline=False)
        embed.add_field(name="Parser Type", value=feed_config["processing"]["content_parser"], inline=True)
        
        intervals = feed_config["check_intervals"]
        interval_text = f"Normal: {intervals['normal']}m | Peak: {intervals['peak']}m | Frequent: {intervals['frequent']}m"
        embed.add_field(name="Check Intervals", value=interval_text, inline=False)
        
        embed.add_field(
            name="History", 
            value=f"{'✅ Enabled' if feed_config['history']['enabled'] else '❌ Disabled'} (Max: {feed_config['history']['max_entries']})", 
            inline=True
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


# Bot setup and run
if __name__ == "__main__":
    # Environment variables (create a .env file or set these)
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")

    if not TOKEN:
        logger.error("DISCORD_BOT_TOKEN environment variable not set")
        exit(1)

    if not CHANNEL_ID:
        logger.error("DISCORD_CHANNEL_ID environment variable not set")
        exit(1)

    bot = FieldStatusBot()

    try:
        bot.run(TOKEN)
    except Exception as e:
        logger.error(f"Failed to run bot: {e}")
