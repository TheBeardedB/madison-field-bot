#!/usr/bin/env python3
"""
Clear Global Slash Commands - Automated
"""

import asyncio
import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

load_dotenv()

async def clear_global_commands():
    """Clear all global slash commands"""
    
    TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    if not TOKEN:
        print("❌ DISCORD_BOT_TOKEN not found in .env file")
        return
    
    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix='/', intents=intents)
    
    @bot.event
    async def on_ready():
        print(f'🤖 Connected as {bot.user.name}')
        
        try:
            # Clear global commands
            bot.tree.clear_commands(guild=None)
            await bot.tree.sync()
            print("✅ Cleared all global commands")
            
        except Exception as e:
            print(f'❌ Failed to clear commands: {e}')
        
        finally:
            await bot.close()
    
    await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(clear_global_commands())
    except Exception as e:
        print(f"Error: {e}")