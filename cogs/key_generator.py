import os
import random
import string
import datetime
from typing import Optional
import discord
from discord.ext import commands
from discord import app_commands
from pymongo import MongoClient

class KeyManagerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        try:
            self.mongo_client = MongoClient(os.getenv("MONGO_URL"))
            self.db = self.mongo_client["key-manager"]
            self.storage_collection = self.db["storage"]
            self.redeem_role_id = None
            self.views_collection = self.db["views"]  # New collection to store active views
        except Exception as e:
            print(f"MongoDB Connection Error: {e}")
            raise

    @commands.Cog.listener()
    async def on_ready(self):
        try:
            self.redeem_role_id = await self.get_redeem_role_id()
            if self.redeem_role_id:
                print(f"Key Manager Cog ready! Redeem role set to {self.redeem_role_id}")
            else:
                print("No redeem role configured.")

            # Rebuild and reattach views from the saved data
            saved_views = self.views_collection.find({"state": "active"})
            for saved_view in saved_views:
                channel_id = saved_view.get("channel_id")
                message_id = saved_view.get("message_id")

                # Ensure both channel_id and message_id exist
                if channel_id and message_id:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        try:
                            # Recreate the view and attach it to the message
                            view = KeyManagerCog.KeyActionsView(self.bot)
                            message = await channel.fetch_message(message_id)
                            await message.edit(view=view)
                        except discord.NotFound:
                            print(f"Message with ID {message_id} not found in channel {channel_id}.")
                        except Exception as e:
                            print(f"Failed to reattach view: {e}")
                    else:
                        print(f"Channel with ID {channel_id} not found.")
                else:
                    print(f"Missing channel_id or message_id in saved view: {saved_view}")

        except Exception as e:
            print(f"Error in on_ready: {e}")

    async def get_redeem_role_id(self) -> Optional[int]:
        try:
            config = self.storage_collection.find_one({"_id": "redeem_role"})
            return config.get("role_id") if config else None
        except Exception as e:
            print(f"Error retrieving redeem role: {e}")
            return None

    @app_commands.command(name="set_redeem_role")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_redeem_role(self, interaction: discord.Interaction, role: discord.Role):
        try:
            self.redeem_role_id = role.id
            self.storage_collection.update_one(
                {"_id": "redeem_role"}, 
                {"$set": {"role_id": role.id}}, 
                upsert=True
            )
            await interaction.response.send_message(f"Redeem role set to {role.name}", ephemeral=True)
        except Exception as e:
            print(f"Error setting redeem role: {e}")
            await interaction.response.send_message("An error occurred while setting the redeem role.", ephemeral=True)

    @app_commands.command(name="create_embed")
    @app_commands.checks.has_permissions(administrator=True)
    async def create_embed(self, interaction: discord.Interaction):
        modal = self.EmbedCreationModal(self.bot)
        await interaction.response.send_modal(modal)

    class EmbedCreationModal(discord.ui.Modal, title="Create Embed"):
        title_input = discord.ui.TextInput(
            label="Title", 
            style=discord.TextStyle.short, 
            required=True, 
            max_length=256
        )
        description_input = discord.ui.TextInput(
            label="Description", 
            style=discord.TextStyle.paragraph, 
            required=True, 
            max_length=2048
        )
        image_url_input = discord.ui.TextInput(
            label="Image URL (optional)", 
            style=discord.TextStyle.short, 
            required=False, 
            max_length=512
        )
        color_input = discord.ui.TextInput(
            label="Color (Hex Code)", 
            style=discord.TextStyle.short, 
            required=True, 
            max_length=7, 
            default="#2f3136"
        )

        def __init__(self, bot: commands.Bot):
            super().__init__(timeout=180)
            self.bot = bot

        async def on_submit(self, interaction: discord.Interaction):
            try:
                title = self.title_input.value
                description = self.description_input.value
                image_url = self.image_url_input.value
                color_hex = self.color_input.value

                if not self.validate_color(color_hex):
                    await interaction.response.send_message("Invalid color code. Use #RRGGBB format.", ephemeral=True)
                    return

                # If an image URL is provided, validate it
                if image_url:
                    if not self.validate_image_url(image_url):
                        await interaction.response.send_message("Invalid image URL.", ephemeral=True)
                        return
                    # If the URL is valid, continue with the embed creation
                    embed = discord.Embed(
                        title=title, 
                        description=description, 
                        color=int(color_hex[1:], 16)
                    )
                    embed.set_image(url=image_url)
                else:
                    # If no image URL, just create the embed without an image
                    embed = discord.Embed(
                        title=title, 
                        description=description, 
                        color=int(color_hex[1:], 16)
                    )

                view = KeyManagerCog.KeyActionsView(self.bot)
                message = await interaction.channel.send(embed=embed, view=view)

                # Store the view state in the database
                self.bot.cogs["KeyManagerCog"].views_collection.insert_one({
                    "state": "active",
                    "channel_id": interaction.channel.id,
                    "message_id": message.id,
                })

                await interaction.response.send_message("Embed created and sent successfully!", ephemeral=True)

            except Exception as e:
                print(f"Embed creation error: {e}")
                await interaction.response.send_message("An unexpected error occurred.", ephemeral=True)

        @staticmethod
        def validate_color(color_hex: str) -> bool:
            try:
                return (
                    len(color_hex) == 7 and 
                    color_hex.startswith("#") and 
                    all(c in "0123456789ABCDEFabcdef" for c in color_hex[1:])
                )
            except Exception:
                return False

        @staticmethod
        def validate_image_url(url: str) -> bool:
            """Validate both external image URLs and Discord attachments."""
            # Check if URL is a valid image URL (ends with common image extensions)
            if url.lower().endswith((".jpg", ".png", ".jpeg", ".gif", ".webp")):
                return True
            # Check if URL is a Discord attachment link (starts with the Discord CDN link)
            return url.startswith("https://cdn.discordapp.com/attachments/") and any(
                url.lower().endswith(ext) for ext in [".jpg", ".png", ".jpeg", ".gif", ".webp"]
            )
            
    class KeyActionsView(discord.ui.View):
        def __init__(self, bot: commands.Bot):
            super().__init__(timeout=None)  # No timeout for buttons
            self.bot = bot
            self.cog = bot.get_cog("KeyManagerCog")

        @discord.ui.button(label="Generate Key", style=discord.ButtonStyle.secondary)
        async def generate_key(self, interaction: discord.Interaction, button: discord.ui.Button):
            try:
                key = self.generate_unique_key()
                await self.store_key(interaction.user.id, key)
                await interaction.response.send_message(f"{key}", ephemeral=True)
            except Exception as e:
                print(f"Key generation error: {e}")
                await interaction.response.send_message("Failed to generate key.", ephemeral=True)

        @discord.ui.button(label="Redeem Key", style=discord.ButtonStyle.secondary)
        async def redeem_key(self, interaction: discord.Interaction, button: discord.ui.Button):
            modal = KeyManagerCog.KeyRedemptionModal(self.bot)
            await interaction.response.send_modal(modal)

        def generate_unique_key(self) -> str:
            attempts = 0
            while attempts < 10:
                key = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
                if not self.cog.storage_collection.find_one({"key": key}):
                    return key
                attempts += 1
            raise ValueError("Could not generate unique key after 10 attempts.")

        async def store_key(self, user_id: int, key: str):
            expiration_date = datetime.datetime.now() + datetime.timedelta(days=30)
            self.cog.storage_collection.insert_one({
                "key": key, 
                "status": "active", 
                "created_at": datetime.datetime.now(), 
                "expiration_date": expiration_date, 
                "user_id": user_id
            })

    class KeyRedemptionModal(discord.ui.Modal, title="Redeem Key"):
        key_input = discord.ui.TextInput(
            label="Enter your key", 
            style=discord.TextStyle.short, 
            required=True, 
            max_length=8
        )

        def __init__(self, bot: commands.Bot):
            super().__init__(timeout=60)
            self.bot = bot

        async def on_submit(self, interaction: discord.Interaction):
            key = self.key_input.value.upper()
            key_doc = self.bot.cogs["KeyManagerCog"].storage_collection.find_one({"key": key})

            if not key_doc or key_doc["status"] == "redeemed":
                await interaction.response.send_message("Invalid or redeemed key.", ephemeral=True)
                return

            if key_doc["expiration_date"] < datetime.datetime.now():
                await interaction.response.send_message("Key expired.", ephemeral=True)
                return

            self.bot.cogs["KeyManagerCog"].storage_collection.update_one(
                {"key": key}, 
                {"$set": {"status": "redeemed", "redeemed_at": datetime.datetime.now()}}
            )

            if self.bot.cogs["KeyManagerCog"].redeem_role_id:
                role = interaction.guild.get_role(self.bot.cogs["KeyManagerCog"].redeem_role_id)
                if role:
                    await interaction.user.add_roles(role)

            await interaction.response.send_message("Key redeemed!", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(KeyManagerCog(bot))
