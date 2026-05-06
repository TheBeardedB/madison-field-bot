import os
# Set environment variable for Python to use UTF-8
os.environ['PYTHONIOENCODING'] = 'utf-8'

import discord
from discord.ext import tasks, commands
from discord import app_commands
import feedparser
import json
import logging
import re
import asyncio
from datetime import datetime, timedelta
import pytz
import aiohttp
from typing import Dict, List, Optional, Tuple
import random
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Set console encoding to UTF-8 for Windows
if os.name == 'nt':  # Windows
    try:
        import subprocess
        subprocess.run(['chcp', '65001'], shell=True, capture_output=True)
    except:
        pass  # Ignore if chcp fails

# Configure logging with proper Unicode handling
import sys

# Create file handler with UTF-8 encoding
file_handler = logging.FileHandler("field_status_bot.log", encoding='utf-8')
file_handler.setLevel(logging.INFO)

# Create console handler with UTF-8 encoding (fallback for Windows)
try:
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.stream.reconfigure(encoding='utf-8')
except (AttributeError, OSError):
    # Fallback for older Python or systems that don't support reconfigure
    console_handler = logging.StreamHandler(sys.stdout)

console_handler.setLevel(logging.INFO)

# Set formatter
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

# Configure root logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

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

        # Configuration
        self.CONFIG_FILE = "bot_config.json"

        # Load configuration (role pings, etc.)
        self.config = self.load_config()

        # Default timezone (can be overridden per guild)
        self.CST = pytz.timezone("US/Central")

        # Legacy fields for backward compatibility during migration
        self.CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
        self.HISTORY_FILE = "field_status_history.json"
        self.current_status = None
        self.last_pub_date = None
        self.status_history = self.load_history()
        self.next_expected_update = None

        # Color mapping
        self.STATUS_COLORS = {
            "open": 0x00FF00,  # Green
            "partial": 0xFF8C00,  # Orange
            "closed": 0xFF0000,  # Red
        }

    def load_history(self) -> List[Dict]:
        """Load status history from file"""
        try:
            if os.path.exists(self.HISTORY_FILE):
                with open(self.HISTORY_FILE, "r") as f:
                    data = json.load(f)
                    # If it's the old format (just a list), convert it
                    if isinstance(data, list):
                        history = data
                        # Try to get last pub date from most recent entry
                        if history:
                            last_entry = history[-1]
                            self.last_pub_date = last_entry.get("pub_date")
                            logger.info(
                                f"Loaded last pub date from history: {self.last_pub_date}"
                            )
                        return history
                    # New format with metadata
                    else:
                        self.last_pub_date = data.get("last_pub_date")
                        logger.info(
                            f"Loaded last pub date from metadata: {self.last_pub_date}"
                        )
                        return data.get("history", [])
        except Exception as e:
            logger.error(f"Error loading history: {e}")
        return []

    def load_config(self) -> Dict:
        """Load bot configuration from file"""
        default_config = {
            "guilds": {}  # Format: {guild_id: guild_config}
        }
        
        # Default guild config structure
        default_guild_config = {
            "feeds": {},  # Format: {feed_id: feed_config}
            "role_pings": {},  # Format: {channel_id: [role_id1, role_id2, ...]}
            "global_settings": {
                "timezone": "US/Central",
                "default_embed_color": 0x3498DB
            }
        }
        
        # Default feed config structure
        default_feed_config = {
            "name": "RSS Feed",
            "url": "",
            "channel_id": None,
            "enabled": True,
            "check_intervals": {
                "normal": 20,  # minutes
                "peak": 5,     # minutes
                "frequent": 1,  # minute (for expected updates)
                "weather": 5   # minutes (for weather-based)
            },
            "schedule": {
                "peak_times": [  # Format: [{"start": "14:30", "end": "15:30", "days": [0,1,2,3,4]}]
                    {"start": "14:30", "end": "15:30", "days": [0,1,2,3,4]},  # Weekdays 2:30-3:30 PM
                    {"start": "07:30", "end": "08:30", "days": [5,6]}         # Weekends 7:30-8:30 AM
                ],
                "weather_check": False,  # Enable weather-based checking
                "weather_location": ""   # Location for weather checks
            },
            "processing": {
                "content_parser": "generic",  # "generic", "field_status", "custom"
                "custom_parser_function": None,
                "filters": [],  # List of filters to apply
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
                "fields": []  # Custom fields to add
            },
            "history": {
                "enabled": True,
                "max_entries": 100,
                "file_path": None  # Will be auto-generated if None
            }
        }

        try:
            if os.path.exists(self.CONFIG_FILE):
                with open(self.CONFIG_FILE, "r", encoding='utf-8') as f:
                    config = json.load(f)
                    
                    # Migration from old config format
                    if "role_pings" in config and "guilds" not in config:
                        logger.info("Migrating from old config format to new guild-based format")
                        # Create a default guild entry with the old config
                        guild_id = str(self.guilds[0].id) if self.guilds else "default"
                        migrated_config = {
                            "guilds": {
                                guild_id: {
                                    "feeds": {
                                        "default_feed": {
                                            **default_feed_config,
                                            "name": "Madison Field Status",
                                            "url": "https://www.madisonal.gov/RSSFeed.aspx?ModID=1&CID=Field-Status-6",
                                            "channel_id": self.CHANNEL_ID,
                                            "processing": {
                                                **default_feed_config["processing"],
                                                "content_parser": "field_status"
                                            }
                                        }
                                    },
                                    "role_pings": config.get("role_pings", {}),
                                    "global_settings": default_guild_config["global_settings"]
                                }
                            }
                        }
                        config = migrated_config
                        # Save the migrated config
                        self.save_config_data(config)
                    
                    # Ensure all guild configs have required structure
                    if "guilds" in config:
                        for guild_id, guild_config in config["guilds"].items():
                            # Merge with default guild config
                            for key, value in default_guild_config.items():
                                if key not in guild_config:
                                    guild_config[key] = value
                            
                            # Ensure all feeds have required structure
                            for feed_id, feed_config in guild_config["feeds"].items():
                                for key, value in default_feed_config.items():
                                    if key not in feed_config:
                                        feed_config[key] = value
                    
                    return config
        except Exception as e:
            logger.error(f"Error loading config: {e}")

        return default_config

    def save_config(self):
        """Save bot configuration to file"""
        self.save_config_data(self.config)
    
    def save_config_data(self, config_data):
        """Save specific config data to file"""
        try:
            with open(self.CONFIG_FILE, "w", encoding='utf-8') as f:
                json.dump(config_data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving config: {e}")
    
    def get_guild_config(self, guild_id: int) -> Dict:
        """Get configuration for a specific guild"""
        guild_id_str = str(guild_id)
        if guild_id_str not in self.config["guilds"]:
            # Create default guild config
            self.config["guilds"][guild_id_str] = {
                "feeds": {},
                "role_pings": {},
                "global_settings": {
                    "timezone": "US/Central",
                    "default_embed_color": 0x3498DB
                }
            }
            self.save_config()
        return self.config["guilds"][guild_id_str]
    
    def get_feed_config(self, guild_id: int, feed_id: str) -> Dict:
        """Get configuration for a specific feed in a guild"""
        guild_config = self.get_guild_config(guild_id)
        return guild_config["feeds"].get(feed_id)

    def save_history(self):
        """Save status history to file with metadata"""
        try:
            data = {"last_pub_date": self.last_pub_date, "history": self.status_history}
            with open(self.HISTORY_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving history: {e}")

    async def fetch_rss_feed(self) -> Optional[feedparser.FeedParserDict]:
        """Fetch RSS feed with proper headers to bypass robots.txt restrictions"""
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; FieldStatusBot/1.0; +http://your-domain.com/bot)",
            "Accept": "application/rss+xml, application/xml, text/xml",
            "Cache-Control": "no-cache",
        }

        try:
            async with aiohttp.ClientSession(
                headers=headers, timeout=aiohttp.ClientTimeout(total=30)
            ) as session:
                async with session.get(self.RSS_URL) as response:
                    if response.status == 200:
                        content = await response.text()
                        return feedparser.parse(content)
                    else:
                        logger.error(f"HTTP {response.status} when fetching RSS feed")
                        return None
        except Exception as e:
            logger.error(f"Error fetching RSS feed: {e}")
            return None

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
        self, channel: discord.TextChannel, embed: discord.Embed
    ):
        """Send status update to channel with optional role ping"""
        # Check if there are configured role pings for this channel
        channel_id_str = str(channel.id)
        role_ids = self.config["role_pings"].get(channel_id_str, [])

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
                # Include embed title in message for notifications
                embed_title = embed.title if embed.title else "Field Status Update"
                message_content = f"{embed_title}\n\n" + " ".join(role_mentions)

        await channel.send(content=message_content, embed=embed)

    async def determine_next_check_interval(self) -> int:
        """Determine the next check interval based on time, expected updates, and weather"""
        now = datetime.now(self.CST)

        # Check if we have a specific expected update time
        if self.next_expected_update:
            time_until_update = (self.next_expected_update - now).total_seconds() / 60

            # Clear expired expected updates (more than 2 hours past)
            if time_until_update < -120:
                logger.info("Expected update is more than 2 hours past, clearing")
                self.next_expected_update = None
            else:
                # Within 10 minutes either side of expected update: 1 minute interval
                if -10 <= time_until_update <= 10:
                    logger.info(
                        f"Within 10 minutes of expected update ({time_until_update:.1f}min), using 1-minute checks"
                    )
                    return self.frequent_interval
                
                # 30 minutes before to 2 hours after expected update: 5 minute interval
                elif -120 <= time_until_update <= 30:
                    logger.info(
                        f"Within expected update window ({time_until_update:.1f}min), using 5-minute checks"
                    )
                    return self.peak_interval

        # Check weather conditions for rain/storms
        try:
            weather_check = await self.check_weather_conditions()
            if weather_check:
                logger.info("Rain/storms forecast, using weather-based frequent checks")
                return self.weather_interval
        except Exception as e:
            logger.error(f"Error checking weather: {e}")

        # Peak time checking (2:30 PM - 3:30 PM CST on weekdays) - fallback for regular 3pm updates
        if now.weekday() < 5:  # Monday = 0, Sunday = 6
            peak_start = now.replace(
                hour=14, minute=30, second=0, microsecond=0
            )  # 2:30 PM
            peak_end = now.replace(
                hour=15, minute=30, second=0, microsecond=0
            )  # 3:30 PM

            if peak_start <= now <= peak_end:
                logger.info("Peak update time (fallback), using 5-minute checks")
                return self.peak_interval

        # Weekend morning checks (7:30 AM - 8:30 AM CST)
        if now.weekday() >= 5:  # Weekend
            weekend_start = now.replace(hour=7, minute=30, second=0, microsecond=0)
            weekend_end = now.replace(hour=8, minute=30, second=0, microsecond=0)

            if weekend_start <= now <= weekend_end:
                logger.info("Weekend morning update time, using 5-minute checks")
                return self.peak_interval

        return self.normal_interval

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

    async def fetch_rss_feed_url(self, url: str) -> Optional[feedparser.FeedParserDict]:
        """Fetch RSS feed from a specific URL"""
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; GenericRSSBot/1.0; +http://your-domain.com/bot)",
            "Accept": "application/rss+xml, application/xml, text/xml",
            "Cache-Control": "no-cache",
        }

        try:
            async with aiohttp.ClientSession(
                headers=headers, timeout=aiohttp.ClientTimeout(total=30)
            ) as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        content = await response.text()
                        return feedparser.parse(content)
                    else:
                        logger.error(f"HTTP {response.status} when fetching RSS feed from {url}")
                        return None
        except Exception as e:
            logger.error(f"Error fetching RSS feed from {url}: {e}")
            return None

    async def parse_feed_content(self, feed_config: dict, content: str, title: str, entry: dict) -> dict:
        """Parse feed content based on the configured parser type"""
        parser_type = feed_config["processing"]["content_parser"]
        
        if parser_type == "field_status":
            # Use the existing field status parser
            status, closed_fields, contains_soccer = self.parse_field_status(content)
            return {
                "status": status,
                "closed_fields": closed_fields,
                "contains_soccer": contains_soccer,
                "title": title,
                "content": content,
                "parser_type": "field_status"
            }
        elif parser_type == "generic":
            # Generic RSS parser - just format the content
            return {
                "title": title,
                "content": content,
                "parser_type": "generic"
            }
        elif parser_type == "custom":
            # Custom parser - placeholder for user-defined parsing
            return {
                "title": title,
                "content": content,
                "parser_type": "custom",
                "raw_entry": entry
            }
        else:
            # Default to generic
            return {
                "title": title,
                "content": content,
                "parser_type": "generic"
            }

    async def process_rss_feed(self, guild_id: int, feed_id: str, feed_config: dict):
        """Process a single RSS feed"""
        try:
            # Fetch RSS feed
            feed = await self.fetch_rss_feed_url(feed_config["url"])
            if not feed or not feed.entries:
                logger.warning(f"No RSS entries found for feed {feed_id}")
                return

            # Get the latest entry
            latest_entry = feed.entries[0]
            content = latest_entry.get("summary", "") or latest_entry.get("description", "")
            pub_date = latest_entry.get("published", "")
            title = latest_entry.get("title", "RSS Update")

            # Check if this is a new update based on pubDate
            feed_key = f"{guild_id}:{feed_id}"
            if not hasattr(self, '_feed_last_pub_dates'):
                self._feed_last_pub_dates = {}
                
            last_pub_date = self._feed_last_pub_dates.get(feed_key)
            if pub_date == last_pub_date:
                logger.debug(f"No new updates for feed {feed_id}. Current pub date: {pub_date}")
                return

            logger.info(f"🆕 RSS update detected for feed {feed_id}! New pub date: {pub_date}, Previous: {last_pub_date}")

            # Parse content based on parser type
            parsed_data = await self.parse_feed_content(feed_config, content, title, latest_entry)
            
            # Create embed
            embed = await self.create_feed_embed(feed_config, parsed_data, pub_date)
            
            # Send update to the configured channel
            channel = self.get_channel(feed_config["channel_id"])
            if channel:
                guild_config = self.get_guild_config(guild_id)
                await self.send_feed_update(channel, embed, guild_config["role_pings"])
                logger.info(f"Posted RSS update for feed {feed_id} to {channel.name}")
            else:
                logger.error(f"Channel {feed_config['channel_id']} not found for feed {feed_id}")

            # Update last pub date
            self._feed_last_pub_dates[feed_key] = pub_date
            
            # Save to history if enabled
            if feed_config["history"]["enabled"]:
                await self.save_feed_history(guild_id, feed_id, feed_config, parsed_data, pub_date)

        except Exception as e:
            logger.error(f"Error processing RSS feed {feed_id}: {e}")

    async def create_feed_embed(self, feed_config: dict, parsed_data: dict, pub_date: str) -> discord.Embed:
        """Create Discord embed for RSS feed update"""
        embed_template = feed_config["embed_template"]
        processing_config = feed_config["processing"]
        
        # Determine color based on content type
        if parsed_data["parser_type"] == "field_status":
            status = parsed_data.get("status", "default")
            color = processing_config["status_colors"].get(status, processing_config["status_colors"]["default"])
            
            # Use field status specific title
            if status == "open":
                title = "🟢 Fields Open"
            elif status == "closed":
                title = "🔴 All Fields Closed"
            elif status == "partial":
                title = "🟡 Some Fields Closed"
            else:
                title = parsed_data["title"]
        else:
            color = processing_config["status_colors"]["default"]
            title = embed_template["title_template"].format(
                title=parsed_data["title"],
                content=parsed_data["content"][:100] + "..." if len(parsed_data["content"]) > 100 else parsed_data["content"]
            )
        
        # Create embed
        embed = discord.Embed(
            title=title,
            color=color,
            timestamp=datetime.now()
        )
        
        # Add content based on parser type
        if parsed_data["parser_type"] == "field_status" and parsed_data.get("status") == "partial":
            # Use the location-based field display for partial closures
            field_categories = self.categorize_fields_by_location(parsed_data.get("closed_fields", []))
            
            # Dublin section
            dublin_value = "Open" if not field_categories["dublin"] else "\n".join([f"• {field}" for field in field_categories["dublin"]])
            embed.add_field(name="Dublin", value=dublin_value, inline=True)
            
            # Palmer section  
            palmer_value = "Open" if not field_categories["palmer"] else "\n".join([f"• {field}" for field in field_categories["palmer"]])
            embed.add_field(name="Palmer", value=palmer_value, inline=True)
            
            # Other section
            other_value = "Open" if not field_categories["other"] else "\n".join([f"• {field}" for field in field_categories["other"]])
            embed.add_field(name="Other", value=other_value, inline=True)
        elif parsed_data["parser_type"] == "field_status" and parsed_data.get("closed_fields"):
            # Regular field status display
            embed.add_field(
                name="Closed Fields",
                value="\n".join([f"• {field}" for field in parsed_data["closed_fields"]]),
                inline=False
            )
        
        # Add description
        description = embed_template["description_template"].format(
            title=parsed_data["title"],
            content=parsed_data["content"][:500] + "..." if len(parsed_data["content"]) > 500 else parsed_data["content"]
        )
        if description.strip() and description != parsed_data["content"]:
            embed.add_field(name="Details", value=description, inline=False)
        elif parsed_data["parser_type"] != "field_status":
            # For non-field status, add content as description
            embed.description = parsed_data["content"][:2000]  # Discord limit
        
        # Add custom fields from template
        for field_template in embed_template.get("fields", []):
            embed.add_field(
                name=field_template["name"],
                value=field_template["value"],
                inline=field_template.get("inline", False)
            )
        
        # Set footer
        embed.set_footer(text=embed_template["footer_text"])
        
        # Set thumbnail if configured
        if embed_template.get("thumbnail_url"):
            embed.set_thumbnail(url=embed_template["thumbnail_url"])
            
        return embed

    async def send_feed_update(self, channel: discord.TextChannel, embed: discord.Embed, role_pings: dict):
        """Send RSS feed update to channel with role pings"""
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

    async def save_feed_history(self, guild_id: int, feed_id: str, feed_config: dict, parsed_data: dict, pub_date: str):
        """Save feed history entry"""
        try:
            history_file = feed_config["history"]["file_path"]
            if not history_file:
                history_file = f"feed_history_{guild_id}_{feed_id}.json"
            
            # Load existing history
            history_data = {"entries": []}
            if os.path.exists(history_file):
                try:
                    with open(history_file, "r", encoding='utf-8') as f:
                        history_data = json.load(f)
                except:
                    pass  # Use default if file is corrupted
            
            # Add new entry
            entry = {
                "timestamp": datetime.now().isoformat(),
                "pub_date": pub_date,
                "parsed_data": parsed_data
            }
            
            history_data["entries"].append(entry)
            
            # Keep only max entries
            max_entries = feed_config["history"]["max_entries"]
            if len(history_data["entries"]) > max_entries:
                history_data["entries"] = history_data["entries"][-max_entries:]
            
            # Save history
            with open(history_file, "w", encoding='utf-8') as f:
                json.dump(history_data, f, indent=2)
                
        except Exception as e:
            logger.error(f"Error saving history for feed {feed_id}: {e}")

    @check_rss_feeds.before_loop
    async def before_check_rss_feeds(self):
        """Wait for bot to be ready before starting the loop"""
        await self.wait_until_ready()
        logger.info("Starting RSS feed monitoring")

    async def setup_hook(self):
        """Set up slash commands by manually registering them to the tree"""
        logger.info("Setting up slash commands...")
        
        # Commands will be added here - for now just log that setup is complete
        logger.info("Slash command setup complete")

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

    async def on_ready(self):
        """Called when bot is ready"""
        logger.info(f"Bot logged in as {self.user.name} ({self.user.id})")
        
        if not self.check_rss_feeds.is_running():
            self.check_rss_feeds.start()
            logger.info("Started RSS feed monitoring task")


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
