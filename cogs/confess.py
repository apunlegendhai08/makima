import discord
from discord import app_commands
from discord.ext import commands
import os
import asyncio
from typing import Optional
from datetime import datetime
import aiohttp
import io
from pymongo import MongoClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class ConfigManager:
    def __init__(self):
        # MongoDB connection using credentials from environment variables
        self.client = MongoClient(os.getenv('MONGO_URL'))
        self.db = self.client['confessions']
        self.guild_collection = self.db['guild_settings']
        self.confessions_collection = self.db['confessions']

    def get_guild_settings(self, guild_id: str):
        guild_settings = self.guild_collection.find_one({"guild_id": guild_id})
        return guild_settings or {}

    def update_guild_settings(self, guild_id: str, new_settings: dict):
        self.guild_collection.update_one(
            {"guild_id": guild_id},
            {"$set": new_settings},
            upsert=True
        )

    def add_confession(self, guild_id: str, message_id: str, author_id: str, title: Optional[str], content: str):
        confession_data = {
            "guild_id": guild_id,
            "message_id": message_id,
            "author_id": str(author_id),
            "title": title,
            "content": content,
            "timestamp": datetime.utcnow().isoformat()
        }
        self.confessions_collection.insert_one(confession_data)

    def get_confession_stats(self, guild_id: str):
        total_confessions = self.confessions_collection.count_documents({"guild_id": guild_id})
        unique_users = len(self.confessions_collection.distinct("author_id", {"guild_id": guild_id}))
        return total_confessions, unique_users

class ConfessionView(discord.ui.View):
    def __init__(self, timeout=None):
        super().__init__(timeout=timeout)

    @discord.ui.button(label="Reply", style=discord.ButtonStyle.secondary, custom_id="confession_reply")
    async def reply(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ConfessionModal(is_reply=True, original_message_id=interaction.message.id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Report", style=discord.ButtonStyle.danger, custom_id="confession_report")
    async def report(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = ConfigManager()
        guild_settings = config.get_guild_settings(str(interaction.guild_id))
        log_channel_id = guild_settings.get('log_channel')

        if log_channel_id:
            log_channel = interaction.guild.get_channel(int(log_channel_id))
            if log_channel:
                report_embed = discord.Embed(
                    title="Confession Report",
                    description=f"**Reported Message ID:** {interaction.message.id}\n"
                              f"**Reported by:** {interaction.user} (ID: {interaction.user.id})\n"
                              f"**Original Content:**\n{interaction.message.embeds[0].description}",
                    color=discord.Color.red(),
                    timestamp=discord.utils.utcnow()
                )
                await log_channel.send(embed=report_embed)

        await interaction.response.send_message("Report submitted to moderators.", ephemeral=True)

class ConfessionModal(discord.ui.Modal):
    def __init__(self, is_reply=False, original_message_id=None):
        super().__init__(title="Submit a Confession" if not is_reply else "Reply to Confession")
        self.is_reply = is_reply
        self.original_message_id = original_message_id

        self.title_input = discord.ui.TextInput(
            label="Title (Optional)",
            style=discord.TextStyle.short,
            placeholder="Enter a title for your confession (optional)",
            required=False,
            max_length=100
        )

        self.confession_input = discord.ui.TextInput(
            label="Your Message",
            style=discord.TextStyle.paragraph,
            placeholder="Type your message here...",
            required=True,
            max_length=2000
        )

        self.attachment_url = discord.ui.TextInput(
            label="Attachment URL (Optional)",
            style=discord.TextStyle.short,
            placeholder="Paste an image URL here (optional)",
            required=False,
            max_length=200
        )

        self.add_item(self.title_input)
        self.add_item(self.confession_input)
        self.add_item(self.attachment_url)

    async def download_attachment(self, url):
        """Download an image from a URL"""
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.read()
                return discord.File(io.BytesIO(data), filename="attachment.png")

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        config = ConfigManager()
        guild_settings = config.get_guild_settings(str(interaction.guild_id))

        confession_channel_id = guild_settings.get('confession_channel')
        log_channel_id = guild_settings.get('log_channel')
        banned_users = guild_settings.get('banned_users', [])

        if str(interaction.user.id) in banned_users:
            await interaction.followup.send("You are banned from using confessions.", ephemeral=True)
            return

        if not confession_channel_id:
            await interaction.followup.send("Confession channel has not been set up!", ephemeral=True)
            return

        confession_channel = interaction.guild.get_channel(int(confession_channel_id))
        if not confession_channel:
            await interaction.followup.send("Confession channel not found!", ephemeral=True)
            return

        # Download attachment if provided
        file = None
        if self.attachment_url.value:
            file = await self.download_attachment(self.attachment_url.value)

        # Create embed
        embed = discord.Embed(
            title=self.title_input.value if self.title_input.value else None,
            description=self.confession_input.value,
            color=discord.Color.from_str(guild_settings.get('embed_color', '#2f3136')),
            timestamp=discord.utils.utcnow()
        )

        if file:
            embed.set_image(url="attachment://attachment.png")

        try:
            if not self.is_reply:
                view = ConfessionView()
                message = await confession_channel.send(embed=embed, view=view, file=file)

                # Save confession to database
                config.add_confession(
                    guild_id=str(interaction.guild_id),
                    message_id=str(message.id),
                    author_id=interaction.user.id,
                    title=self.title_input.value,
                    content=self.confession_input.value
                )

            else:
                # Handle reply in thread
                original_message = await confession_channel.fetch_message(self.original_message_id)
                thread = original_message.thread
                if not thread:
                    thread = await original_message.create_thread(name="Confession Discussion")
                await thread.send(embed=embed, file=file)

            # Log the confession
            if log_channel_id:
                log_channel = interaction.guild.get_channel(int(log_channel_id))
                if log_channel:
                    log_embed = discord.Embed(
                        title="New Confession Log",
                        description=f"**Author:** {interaction.user} (ID: {interaction.user.id})\n"
                                  f"**Type:** {'Reply' if self.is_reply else 'Original confession'}\n"
                                  f"**Title:** {self.title_input.value or 'None'}\n"
                                  f"**Content:**\n{self.confession_input.value}",
                        color=discord.Color.blue(),
                        timestamp=discord.utils.utcnow()
                    )
                    if file:
                        log_embed.add_field(name="Attachment", value="Image included", inline=False)
                    await log_channel.send(embed=log_embed)

            await interaction.followup.send("Your message has been submitted!", ephemeral=True)

        except discord.HTTPException as e:
            await interaction.followup.send(f"Failed to submit confession: {str(e)}", ephemeral=True)

class Confessions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = ConfigManager()
        bot.add_view(ConfessionView())  # Persistent view registration

    async def cog_load(self):
        """Restore persistent views only for bot-authored messages"""
        for guild in self.bot.guilds:
            try:
                guild_settings = self.config.get_guild_settings(str(guild.id))
                confession_channel_id = guild_settings.get('confession_channel')
                
                if not confession_channel_id:
                    continue

                confession_channel = guild.get_channel(int(confession_channel_id))
                if not confession_channel:
                    continue

                processed_messages = 0
                async for message in confession_channel.history(limit=50):
                    # Only process messages sent by the bot
                    if (message.author == self.bot.user and 
                        message.embeds and 
                        message.embeds[0].description and 
                        len(message.embeds[0].description) > 10):
                        
                        try:
                            view = ConfessionView()
                            await message.edit(view=view)
                            processed_messages += 1
                            
                            # Exponential backoff
                            if processed_messages % 5 == 0:
                                await asyncio.sleep(2)
                            
                            await asyncio.sleep(0.5)
                        
                        except discord.HTTPException as e:
                            print(f"Error editing message {message.id}: {e}")
                            await asyncio.sleep(min(processed_messages * 2, 30))
                        
                        except Exception as e:
                            print(f"Unexpected error with message {message.id}: {e}")
                            await asyncio.sleep(1)

            except Exception as e:
                print(f"Error restoring views for guild {guild.id}: {e}")

    @app_commands.command(name="confess")
    async def confess(self, interaction: discord.Interaction):
        """Submit an anonymous confession"""
        modal = ConfessionModal()
        await interaction.response.send_modal(modal)

    @app_commands.command(name="setconfessionchannel")
    @app_commands.default_permissions(administrator=True)
    async def set_confession_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """Set the channel for confessions"""
        self.config.update_guild_settings(str(interaction.guild_id), {'confession_channel': channel.id})
        await interaction.response.send_message(f"Confession channel set to {channel.mention}", ephemeral=True)

    @app_commands.command(name="setlogchannel")
    @app_commands.default_permissions(administrator=True)
    async def set_log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """Set the channel for confession logs"""
        self.config.update_guild_settings(str(interaction.guild_id), {'log_channel': channel.id})
        await interaction.response.send_message(f"Log channel set to {channel.mention}", ephemeral=True)

    @app_commands.command(name="banuser")
    @app_commands.default_permissions(administrator=True)
    async def ban_user(self, interaction: discord.Interaction, user: discord.User, action: str = "ban"):
        """Ban or unban a user from using confessions"""
        guild_settings = self.config.get_guild_settings(str(interaction.guild_id))

        if 'banned_users' not in guild_settings:
            guild_settings['banned_users'] = []

        if action.lower() == "ban":
            if str(user.id) not in guild_settings['banned_users']:
                guild_settings['banned_users'].append(str(user.id))
            message = f"{user} has been banned from using confessions."
        else:
            if str(user.id) in guild_settings['banned_users']:
                guild_settings['banned_users'].remove(str(user.id))
            message = f"{user} has been unbanned from using confessions."

        self.config.update_guild_settings(str(interaction.guild_id), guild_settings)
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="confessionstats")
    @app_commands.default_permissions(administrator=True)
    async def confession_stats(self, interaction: discord.Interaction):
        """View confession statistics"""
        total_confessions, unique_users = self.config.get_confession_stats(str(interaction.guild_id))

        embed = discord.Embed(
            title="Confession Statistics",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Total Confessions", value=str(total_confessions), inline=True)
        embed.add_field(name="Unique Users", value=str(unique_users), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="setconfessioncolor")
    @app_commands.default_permissions(administrator=True)
    async def set_confession_color(self, interaction: discord.Interaction, color: str):
        """Set the color for confession embeds (hex code)"""
        try:
            # Validate color format
            if not color.startswith('#'):
                color = f'#{color}'
            discord.Color.from_str(color)
            self.config.update_guild_settings(str(interaction.guild_id), {'embed_color': color})
            await interaction.response.send_message(f"Confession embed color set to {color}", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("Invalid color format. Please use hex color code (e.g., #FF0000)", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Confessions(bot))
