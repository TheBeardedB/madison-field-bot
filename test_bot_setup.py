#!/usr/bin/env python3
"""
Test the fixed Discord bot command registration
"""

import asyncio
import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

load_dotenv()

async def test_bot_setup():
    """Test that the bot can start and register commands without errors"""
    
    TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    if not TOKEN:
        print("❌ DISCORD_BOT_TOKEN not found in .env file")
        return
    
    print("🤖 Testing bot startup and command registration...")
    
    # Import the fixed bot
    from field_status_bot import FieldStatusBot
    
    bot = FieldStatusBot()
    
    @bot.event
    async def on_ready():
        print(f'✅ Bot connected as {bot.user.name}')
        print(f'🔍 Registered commands: {len(bot.tree.get_commands())}')
        
        # List all registered commands
        for cmd in bot.tree.get_commands():
            print(f'   - /{cmd.name}: {cmd.description}')
        
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
    print("This will test that the bot can start and register commands properly.")
    print()
    
    try:
        asyncio.run(test_bot_setup())
    except KeyboardInterrupt:
        print("\n⏹️ Test cancelled by user")
    except Exception as e:
        print(f"❌ Test failed: {e}")

if __name__ == "__main__":
    main()
