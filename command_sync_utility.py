#!/usr/bin/env python3
"""
Madison Field Status Bot - Command Sync Utility
Handles proper syncing and clearing of Discord slash commands
"""

import asyncio
import discord
from discord.ext import commands
from discord import app_commands
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class CommandSyncBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix='/', intents=intents)

async def clear_all_commands():
    """Clear all slash commands globally and from all guilds"""
    TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    if not TOKEN:
        print("❌ DISCORD_BOT_TOKEN not found in .env file")
        return

    bot = CommandSyncBot()
    
    @bot.event
    async def on_ready():
        print(f'🤖 Connected as {bot.user.name}')
        
        try:
            # Clear global commands
            print("Clearing global commands...")
            bot.tree.clear_commands(guild=None)
            await bot.tree.sync()
            print("✅ Cleared global commands")
            
            # Clear guild commands for all guilds the bot is in
            print(f"Clearing guild commands from {len(bot.guilds)} guilds...")
            for guild in bot.guilds:
                try:
                    bot.tree.clear_commands(guild=guild)
                    await bot.tree.sync(guild=guild)
                    print(f"✅ Cleared commands from guild: {guild.name}")
                except Exception as e:
                    print(f"⚠️ Failed to clear commands from {guild.name}: {e}")
            
            print("🎉 All commands cleared successfully!")
            
        except Exception as e:
            print(f'❌ Failed to clear commands: {e}')
        
        finally:
            await bot.close()
    
    await bot.start(TOKEN)

async def sync_commands_globally():
    """Sync commands globally (takes up to 1 hour to propagate)"""
    TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    if not TOKEN:
        print("❌ DISCORD_BOT_TOKEN not found in .env file")
        return

    from field_status_bot import FieldStatusBot
    
    bot = FieldStatusBot()
    
    @bot.event
    async def on_ready():
        print(f'🤖 Connected as {bot.user.name}')
        
        try:
            print("Syncing commands globally...")
            synced = await bot.tree.sync()
            print(f'✅ Successfully synced {len(synced)} commands globally')
            
            for command in synced:
                print(f'   - /{command.name}')
            
            print("\n⏰ Note: Global sync can take up to 1 hour to propagate to all servers")
            
        except discord.HTTPException as e:
            if e.status == 429:
                print('⚠️ Rate limited - you may have synced recently. Wait a few minutes.')
            else:
                print(f'❌ HTTP error syncing commands: {e}')
        except Exception as e:
            print(f'❌ Failed to sync commands: {e}')
        
        finally:
            await bot.close()
    
    await bot.start(TOKEN)

async def sync_commands_to_guild():
    """Sync commands to a specific guild (immediate)"""
    TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    if not TOKEN:
        print("❌ DISCORD_BOT_TOKEN not found in .env file")
        return

    guild_id = input("Enter guild ID for immediate sync: ").strip()
    if not guild_id.isdigit():
        print("❌ Guild ID must be numeric")
        return

    from field_status_bot import FieldStatusBot
    
    bot = FieldStatusBot()
    
    @bot.event
    async def on_ready():
        print(f'🤖 Connected as {bot.user.name}')
        
        try:
            guild = discord.Object(id=int(guild_id))
            print(f"Syncing commands to guild {guild_id}...")
            synced = await bot.tree.sync(guild=guild)
            print(f'✅ Successfully synced {len(synced)} commands to guild {guild_id}')
            
            for command in synced:
                print(f'   - /{command.name}')
            
            print("\n🚀 Commands should be available immediately in that server!")
            
        except discord.HTTPException as e:
            if e.status == 403:
                print('❌ Bot lacks permissions in that guild')
            elif e.status == 404:
                print('❌ Guild not found or bot not in that guild')
            else:
                print(f'❌ HTTP error syncing commands: {e}')
        except Exception as e:
            print(f'❌ Failed to sync commands: {e}')
        
        finally:
            await bot.close()
    
    await bot.start(TOKEN)

async def force_resync():
    """Force clear and resync commands globally"""
    print("🔄 Force clearing and resyncing all commands...")
    print("This will clear all existing commands and sync new ones globally.")
    print("Global sync takes up to 1 hour to propagate.")
    
    confirm = input("Continue? (y/N): ").lower().strip()
    if confirm != 'y':
        print("Cancelled.")
        return
    
    # First clear all commands
    print("\nStep 1: Clearing all existing commands...")
    await clear_all_commands()
    
    # Wait a moment
    await asyncio.sleep(2)
    
    # Then sync new commands
    print("\nStep 2: Syncing new commands globally...")
    await sync_commands_globally()

def main():
    print("🚀 Madison Field Bot - Command Sync Utility")
    print("=" * 50)
    print("Choose an option:")
    print("1. Clear all commands (global and guild)")
    print("2. Sync commands globally (takes up to 1 hour)")
    print("3. Sync commands to specific guild (immediate)")
    print("4. Force clear and resync globally")
    print("5. Exit")
    
    while True:
        choice = input("\nEnter choice (1-5): ").strip()
        
        if choice == '1':
            print("\n🧹 Clearing all commands...")
            try:
                asyncio.run(clear_all_commands())
            except KeyboardInterrupt:
                print("\n⏹️ Cancelled")
            except Exception as e:
                print(f"❌ Error: {e}")
            break
            
        elif choice == '2':
            print("\n🌐 Syncing commands globally...")
            try:
                asyncio.run(sync_commands_globally())
            except KeyboardInterrupt:
                print("\n⏹️ Cancelled")
            except Exception as e:
                print(f"❌ Error: {e}")
            break
            
        elif choice == '3':
            print("\n⚡ Syncing to specific guild...")
            try:
                asyncio.run(sync_commands_to_guild())
            except KeyboardInterrupt:
                print("\n⏹️ Cancelled")
            except Exception as e:
                print(f"❌ Error: {e}")
            break
            
        elif choice == '4':
            print("\n🔄 Force clearing and resyncing...")
            try:
                asyncio.run(force_resync())
            except KeyboardInterrupt:
                print("\n⏹️ Cancelled")
            except Exception as e:
                print(f"❌ Error: {e}")
            break
            
        elif choice == '5':
            print("👋 Goodbye!")
            break
            
        else:
            print("❌ Invalid choice. Please enter 1-5.")

if __name__ == "__main__":
    main()
