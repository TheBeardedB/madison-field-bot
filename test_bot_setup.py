#!/usr/bin/env python3
"""
Quick startup smoke test for the field status bot
"""

import asyncio
import discord
import os
from dotenv import load_dotenv

load_dotenv()

async def test_bot_setup():
    """Test that the bot can be created and connect with the configured token."""
    
    TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    if not TOKEN:
        print("❌ DISCORD_BOT_TOKEN not found in .env file")
        return
    
    print("🤖 Testing bot startup...")
    
    from field_status_bot import FieldStatusBot
    
    bot = FieldStatusBot()
    
    @bot.event
    async def on_ready():
        print(f'✅ Bot connected as {bot.user.name}')
        print('📡 The bot is ready to poll the RSS feed and maintain the single status message.')
        print("🎉 Bot setup test completed successfully!")
        await bot.close()
    
    try:
        await bot.start(TOKEN)
    except discord.LoginFailure:
        print("❌ Invalid bot token")
    except Exception as e:
        print(f"❌ Error during bot test: {e}")

def main():
    print("🚀 Testing Fixed Discord Bot")
    print("=" * 40)
    print("This will test that the bot can start and connect properly.")
    print()
    
    try:
        asyncio.run(test_bot_setup())
    except KeyboardInterrupt:
        print("\n⏹️ Test cancelled by user")
    except Exception as e:
        print(f"❌ Test failed: {e}")

if __name__ == "__main__":
    main()
