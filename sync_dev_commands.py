#!/usr/bin/env python3
"""
Madison Field Status Bot - Development Helper
For faster slash command syncing during development
"""

import asyncio
import discord
from discord.ext import commands
from discord import app_commands
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

async def sync_commands_to_guild():
    """Sync commands to a specific guild for faster development testing"""
    
    # Get your guild (server) ID - you need to set this!
    GUILD_ID = input("Enter your Discord server ID for development syncing: ").strip()
    
    if not GUILD_ID:
        print("❌ Guild ID is required for development syncing")
        return
    
    try:
        GUILD_ID = int(GUILD_ID)
    except ValueError:
        print("❌ Guild ID must be numeric")
        return
    
    TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    if not TOKEN:
        print("❌ DISCORD_BOT_TOKEN not found in .env file")
        return
    
    # Create a minimal bot just for syncing
    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix='/', intents=intents)
    
    # Add the same commands as the main bot
    @app_commands.command(name="test_sync", description="Test command to verify syncing works")
    async def test_sync(interaction: discord.Interaction):
        await interaction.response.send_message("✅ Slash commands are working!", ephemeral=True)
    
    @app_commands.command(name="field_help", description="Show field bot commands") 
    async def field_help(interaction: discord.Interaction):
        await interaction.response.send_message("✅ Field help command synced!", ephemeral=True)
    
    # Add commands to tree
    bot.tree.add_command(test_sync)
    bot.tree.add_command(field_help)
    
    @bot.event
    async def on_ready():
        print(f'🤖 Connected as {bot.user.name}')
        
        try:
            # Sync to specific guild (faster)
            guild = discord.Object(id=GUILD_ID)
            synced = await bot.tree.sync(guild=guild)
            print(f'✅ Synced {len(synced)} commands to guild {GUILD_ID}')
            
            for command in synced:
                print(f'   - /{command.name}')
            
            print("\n🎉 Development sync complete!")
            print("Commands should appear immediately in your server.")
            print("You can now test with /test_sync")
            
        except Exception as e:
            print(f'❌ Failed to sync commands: {e}')
        
        finally:
            await bot.close()
    
    try:
        await bot.start(TOKEN)
    except Exception as e:
        print(f'❌ Bot failed to start: {e}')

def main():
    print("🚀 Madison Field Bot - Development Command Sync")
    print("=" * 50)
    print("This script syncs slash commands to a specific server for development.")
    print("This is MUCH faster than global syncing (immediate vs up to 1 hour).")
    print()
    print("⚠️  NOTE: These commands will only work in the specified server!")
    print("   For production, use global syncing in the main bot.")
    print()
    
    choice = input("Continue with guild-specific sync? (y/n): ").lower().strip()
    if choice != 'y':
        print("Cancelled.")
        return
    
    try:
        asyncio.run(sync_commands_to_guild())
    except KeyboardInterrupt:
        print("\n⏹️  Sync cancelled by user")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    main()
