import discord
from discord.ext import commands
from discord import app_commands
from pymongo import MongoClient
from datetime import datetime
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class ThreadCreatorCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # MongoDB setup
        mongo_uri = os.getenv("MONGO_URL")
        if not mongo_uri:
            raise ValueError("MONGO_URL is not set in the environment variables.")

        self.mongo_client = MongoClient(mongo_uri)
        self.db = self.mongo_client["threads"]  # Database name
        self.guild_configs = self.db["guild_configs"]  # Collection for guild configurations
        self.cooldowns = self.db["cooldowns"]  # Collection for cooldown tracking

    def is_on_cooldown(self, guild_id, user_id, cooldown):
        """Check if a user is on cooldown."""
        now = datetime.utcnow()
        cooldown_entry = self.cooldowns.find_one({"guild_id": guild_id, "user_id": user_id})

        if cooldown_entry:
            last_used = cooldown_entry["last_used"]
            time_since_last = (now - last_used).total_seconds()
            if time_since_last < cooldown:
                return True, cooldown - time_since_last

        # Update the cooldown time
        self.cooldowns.update_one(
            {"guild_id": guild_id, "user_id": user_id},
            {"$set": {"last_used": now}},
            upsert=True
        )
        return False, 0

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handle thread creation."""
        if message.author.bot or not message.guild:
            return

        guild_id, channel_id = str(message.guild.id), str(message.channel.id)
        config = self.guild_configs.find_one({"guild_id": guild_id, "channel_id": channel_id})
        if not config or not message.attachments:
            return

        cooldown = config.get("cooldown", 30)
        on_cooldown, remaining = self.is_on_cooldown(guild_id, message.author.id, cooldown)
        if on_cooldown:
            await message.channel.send(f"⏳ Cooldown active. Try again in {remaining:.0f}s.", delete_after=5)
            return

        thread_name = message.content[:50] or f"Thread by {message.author.display_name}"
        thread = await message.create_thread(name=thread_name, auto_archive_duration=1440)
        await thread.send(f"📎 Thread created by {message.author.mention}", delete_after=10)

    @app_commands.command(name="thread_channel", description="Configure a channel for thread creation.")
    async def configure_channel(self, interaction: discord.Interaction, channel: discord.TextChannel, cooldown: int = 30):
        """Set up thread creation for a channel."""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return

        guild_id, channel_id = str(interaction.guild.id), str(channel.id)
        self.guild_configs.update_one(
            {"guild_id": guild_id, "channel_id": channel_id},
            {"$set": {"cooldown": cooldown}},
            upsert=True
        )
        await interaction.response.send_message(f"✅ Thread creation enabled in {channel.mention} with {cooldown}s cooldown.", ephemeral=True)

    @app_commands.command(name="thread_status", description="Check thread settings for this server.")
    async def thread_status(self, interaction: discord.Interaction):
        """Show all configured channels."""
        guild_id = str(interaction.guild.id)
        config_cursor = self.guild_configs.find({"guild_id": guild_id})
        configs = list(config_cursor)

        if not configs:
            await interaction.response.send_message("❌ No channels configured.", ephemeral=True)
            return

        embed = discord.Embed(title="📊 Thread Configuration", color=discord.Color.blue())
        for config in configs:
            channel = interaction.guild.get_channel(int(config["channel_id"]))
            if channel:
                embed.add_field(name=f"#{channel.name}", value=f"Cooldown: {config['cooldown']}s", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(ThreadCreatorCog(bot))
