#!/usr/bin/env python3
"""
Clear Slash Commands - Development Helper
Use this to clear slash commands if needed during development
"""

import asyncio
import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

load_dotenv()

async def clear_commands():
    """Clear all slash commands"""
    
    TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    if not TOKEN:
        print("❌ DISCORD_BOT_TOKEN not found in .env file")
        return
    
    print("Choose what to clear:")
    print("1. Clear global commands")
    print("2. Clear guild-specific commands") 
    print("3. Clear both")
    
    choice = input("Enter choice (1-3): ").strip()
    
    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix='/', intents=intents)
    
    @bot.event
    async def on_ready():
        print(f'🤖 Connected as {bot.user.name}')
        
        try:
            if choice in ['1', '3']:
                # Clear global commands
                bot.tree.clear_commands(guild=None)
                await bot.tree.sync()
                print("✅ Cleared global commands")
            
            if choice in ['2', '3']:
                # Clear guild commands
                guild_id = input("Enter guild ID to clear: ").strip()
                if guild_id:
                    guild = discord.Object(id=int(guild_id))
                    bot.tree.clear_commands(guild=guild)
                    await bot.tree.sync(guild=guild)
                    print(f"✅ Cleared commands for guild {guild_id}")
            
        except Exception as e:
            print(f'❌ Failed to clear commands: {e}')
        
        finally:
            await bot.close()
    
    await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(clear_commands())
    except KeyboardInterrupt:
        print("\n⏹️  Cancelled")
    except Exception as e:
        print(f"❌ Error: {e}")
