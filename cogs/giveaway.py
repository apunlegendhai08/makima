import discord
import random
from datetime import datetime
from discord.ext import commands, tasks
import logging
from logging.handlers import RotatingFileHandler
import asyncio
from typing import Optional, List, Dict
import pytz
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Constants
REACTION_EMOJI = "<:sukoon_taaada:1324071825910792223>"
DOT_EMOJI = "<:sukoon_blackdot:1322894649488314378>"
RED_DOT_EMOJI = "<:sukoon_redpoint:1322894737736339459>"
EMBED_COLOR = 0x2f3136
CLEANUP_INTERVAL = 60

class DatabaseManager:
    """Manages MongoDB interactions."""

    def __init__(self, mongo_uri: str, database_name: str):
        self.client = AsyncIOMotorClient(mongo_uri)
        self.db = self.client[database_name]
        self.giveaways_collection = self.db['giveaways']
        self.participants_collection = self.db['participants']

    async def init(self):
        """Initializes indexes for collections."""
        await self.giveaways_collection.create_index('message_id', unique=True)
        await self.participants_collection.create_index([('message_id', 1), ('user_id', 1)], unique=True)

class Giveaway(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        mongo_uri = os.getenv('MONGO_URL')
        database_name = os.getenv('MONGO_DATABASE', 'giveaway_bot')

        # Configure logging
        log_file = os.getenv('LOG_FILE', 'giveaway_logs.log')
        self.logger = logging.getLogger('GiveawayBot')
        handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3)
        handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.WARNING)

        self.db = DatabaseManager(mongo_uri, database_name)
        self._checking = False
        self._ready = asyncio.Event()
        self.check_giveaways.start()

    async def cog_load(self):
        await self.db.init()
        self._ready.set()

    def cog_unload(self):
        self.check_giveaways.cancel()

    async def check_bot_permissions(self, channel):
        """Checks if the bot has required permissions in the given channel."""
        perms = channel.permissions_for(channel.guild.me)
        required = {'send_messages', 'embed_links', 'add_reactions', 'read_message_history'}
        return all(getattr(perms, perm, False) for perm in required)

    @discord.app_commands.command(name="giveaway", description="Start a new giveaway")
    @discord.app_commands.guild_only()
    @discord.app_commands.default_permissions(administrator=True)
    async def start_giveaway(self, interaction: discord.Interaction, duration: str, winners: int, prize: str):
        """Starts a new giveaway."""
        try:
            await interaction.response.defer(ephemeral=True)

            if not await self.check_bot_permissions(interaction.channel):
                await interaction.followup.send("I need proper permissions to start a giveaway.", ephemeral=True)
                return

            if not 1 <= winners <= 20:
                raise ValueError("Winners must be between 1 and 20.")

            # Parse duration
            time_units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
            unit = duration[-1].lower()
            if unit not in time_units or not duration[:-1].isdigit():
                raise ValueError("Use number + s/m/h/d (e.g., 1h, 2d).")
            duration_seconds = int(duration[:-1]) * time_units[unit]
            if not 30 <= duration_seconds <= 2592000:
                raise ValueError("Duration must be between 30 seconds and 30 days.")

            end_timestamp = int(datetime.utcnow().timestamp() + duration_seconds)

            # Send the giveaway title before the embed
            await interaction.channel.send("**<:sukoon_taaada:1324071825910792223> GIVEAWAY <:sukoon_taaada:1324071825910792223>**")

            # Format the end time for the embed footer
            end_time = datetime.utcfromtimestamp(end_timestamp).replace(tzinfo=pytz.utc)
            local_time = end_time.astimezone(pytz.timezone("Asia/Kolkata"))
            formatted_time = local_time.strftime("%A at %I:%M %p") if local_time.date() > datetime.utcnow().date() else local_time.strftime("Today at %I:%M %p")

            # Create and send the giveaway embed
            embed = discord.Embed(
                description=f"{DOT_EMOJI} Ends: <t:{end_timestamp}:R>\n"
                            f"{DOT_EMOJI} Hosted by: {interaction.user.mention}",
                color=EMBED_COLOR
            )
            embed.set_author(name=prize, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
            embed.set_footer(text=f"Ends at • {formatted_time}")

            message = await interaction.channel.send(embed=embed)
            await message.add_reaction(REACTION_EMOJI)

            # Store giveaway in MongoDB
            giveaway_doc = {
                'message_id': str(message.id),
                'channel_id': interaction.channel.id,
                'end_time': end_timestamp,
                'winners': winners,
                'prize': prize,
                'status': 'active',
                'host_id': interaction.user.id,
                'created_at': int(datetime.utcnow().timestamp())
            }
            await self.db.giveaways_collection.insert_one(giveaway_doc)

            await interaction.followup.send("Giveaway started successfully!", ephemeral=True)

        except ValueError as e:
            await interaction.followup.send(f"Error: {str(e)}", ephemeral=True)
        except Exception as e:
            self.logger.error(f"Error starting giveaway: {e}")
            await interaction.followup.send("An unexpected error occurred.", ephemeral=True)

    async def end_giveaway(self, message_id: str):
        """Ends an active giveaway and announces winners."""
        try:
            # Fetch giveaway from MongoDB
            giveaway = await self.db.giveaways_collection.find_one({
                'message_id': message_id, 
                'status': 'active'
            })

            if not giveaway:
                return

            channel = self.bot.get_channel(giveaway['channel_id'])
            if not channel:
                return

            try:
                message = await channel.fetch_message(int(message_id))
            except discord.NotFound:
                await self.db.giveaways_collection.update_one(
                    {'message_id': message_id},
                    {'$set': {'status': 'ended'}}
                )
                return

            # Fetch participants
            participants_cursor = self.db.participants_collection.find({
                'message_id': message_id
            })
            participants = await participants_cursor.to_list(length=None)

            valid_participants = [p['user_id'] for p in participants if p['user_id'] != self.bot.user.id]

            winners = random.sample(valid_participants, min(len(valid_participants), giveaway['winners'])) if valid_participants else []
            winner_mentions = [f"<@{w}>" for w in winners] if winners else ["No winners (no participants)."]

            # Format the end time for the footer
            end_time = datetime.utcnow().replace(tzinfo=pytz.utc)
            local_time = end_time.astimezone(pytz.timezone("Asia/Kolkata"))
            formatted_time = local_time.strftime("%m/%d/%y, %I:%M %p")

            # Update the embed with the results
            embed = discord.Embed(
                description=f"{DOT_EMOJI} Ended: <t:{int(datetime.utcnow().timestamp())}:R>\n"
                            f"{RED_DOT_EMOJI} Winners: {', '.join(winner_mentions)}\n"
                            f"{DOT_EMOJI} Hosted by: <@{giveaway['host_id']}>",
                color=EMBED_COLOR
            )
            embed.set_author(name=giveaway['prize'], icon_url=channel.guild.icon.url if channel.guild.icon else None)
            embed.set_footer(text=f"Ended at • {formatted_time}")
            await message.edit(embed=embed)

            if winners:
                await message.reply(f"{REACTION_EMOJI} Congratulations {', '.join(winner_mentions)}! "
                                    f"You won **{giveaway['prize']}**!")

            # Update giveaway status
            await self.db.giveaways_collection.update_one(
                {'message_id': message_id},
                {'$set': {'status': 'ended'}}
            )

        except Exception as e:
            self.logger.error(f"Error ending giveaway {message_id}: {e}")

    @tasks.loop(seconds=CLEANUP_INTERVAL)
    async def check_giveaways(self):
        """Checks for giveaways that need to be ended."""
        await self._ready.wait()
        if self._checking:
            return

        try:
            self._checking = True
            current_time = int(datetime.utcnow().timestamp())

            # Find active giveaways that have ended
            active_giveaways = await self.db.giveaways_collection.find({
                'end_time': {'$lte': current_time}, 
                'status': 'active'
            }).to_list(length=None)

            for giveaway in active_giveaways:
                await self.end_giveaway(giveaway['message_id'])

        finally:
            self._checking = False

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Handles reaction adds for giveaway participation."""
        if str(payload.emoji) != REACTION_EMOJI or payload.user_id == self.bot.user.id:
            return

        await self.db.participants_collection.update_one(
            {
                'message_id': str(payload.message_id),
                'user_id': payload.user_id
            },
            {'$set': {
                'message_id': str(payload.message_id),
                'user_id': payload.user_id
            }},
            upsert=True
        )

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        """Handles reaction removal for giveaway participation."""
        if str(payload.emoji) != REACTION_EMOJI or payload.user_id == self.bot.user.id:
            return

        await self.db.participants_collection.delete_one({
            'message_id': str(payload.message_id),
            'user_id': payload.user_id
        })

    @commands.command(name="reroll")
    @commands.has_permissions(manage_guild=True)
    async def reroll_giveaway(self, ctx):
        """Reroll winners for a giveaway when replying to its message."""
        # Check if the message being replied to is a giveaway embed
        if not ctx.message.reference or not ctx.message.reference.message_id:
            await ctx.send("Please reply to a giveaway message to reroll.", ephemeral=True)
            return

        try:
            # Fetch the original giveaway message
            original_message = await ctx.channel.fetch_message(ctx.message.reference.message_id)

            # Verify it's a giveaway embed
            if not original_message.embeds or not original_message.embeds[0].author:
                await ctx.send("This doesn't appear to be a giveaway message.", ephemeral=True)
                return

            # Fetch giveaway details from MongoDB
            giveaway = await self.db.giveaways_collection.find_one({
                'message_id': str(original_message.id),
                'status': 'ended'
            })

            if not giveaway:
                await ctx.send("This giveaway hasn't ended or cannot be rerolled.", ephemeral=True)
                return

            # Ensure giveaway['winners'] is always a list
            winners_list = giveaway.get('winners', [])
            if not isinstance(winners_list, list):
                winners_list = [winners_list]  # Make sure it's a list even if it's a single value

            # Fetch participants
            participants_cursor = self.db.participants_collection.find({
                'message_id': str(original_message.id)
            })
            participants = await participants_cursor.to_list(length=None)

            valid_participants = [p['user_id'] for p in participants if p['user_id'] != self.bot.user.id]

            # Fetch previous winners with the corrected query
            previous_winners_cursor = self.db.participants_collection.find({
                'message_id': str(original_message.id),
                'user_id': {'$in': winners_list}  # Use the corrected array
            })
            previous_winners = await previous_winners_cursor.to_list(length=None)
            previous_winner_ids = set(p['user_id'] for p in previous_winners)

            # Remove previous winners from valid participants
            valid_participants = [p for p in valid_participants if p not in previous_winner_ids]

            if not valid_participants:
                await ctx.send("No participants left for rerolling.", ephemeral=True)
                return

            # Reroll winners
            new_winners = random.sample(valid_participants, min(len(valid_participants), giveaway['winners'])) if valid_participants else []
            winner_mentions = [f"<@{w}>" for w in new_winners] if new_winners else ["No winners (no participants)."]

            # Update the original message
            embed = discord.Embed(
                description=f"{DOT_EMOJI} Ended: <t:{int(datetime.utcnow().timestamp())}:R>\n"
                            f"{RED_DOT_EMOJI} Winners: {', '.join(winner_mentions)}\n"
                            f"{DOT_EMOJI} Hosted by: <@{giveaway['host_id']}>",
                color=EMBED_COLOR
            )
            # Send reroll announcement
            if new_winners:
                await ctx.send(f"{REACTION_EMOJI} Congratulations {', '.join(winner_mentions)}! "
                               f"Congratulations on winning **{giveaway['prize']}**!")
            else:
                await ctx.send("Reroll failed due to no participants.")

        except Exception as e:
            self.logger.error(f"Error rerolling giveaway: {e}")
            await ctx.send("An error occurred while rerolling the giveaway.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Giveaway(bot))
