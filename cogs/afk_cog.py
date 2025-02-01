import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

class AFK(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.mongo_uri = os.getenv("MONGO_URL")  # Load MongoDB URI from environment variables
        self.database_name = "discord_bot"  # Name of your database
        self.collection_name = "afk"  # Name of your collection
        self.db_client = None  # MongoDB client
        self.db = None  # Reference to the database
        self.afk_collection = None  # Reference to the AFK collection
        self._cache = {}  # Cache for AFK statuses
        self.cache_expiry_duration = timedelta(hours=24)  # Cache expires after 24 hours

    async def init_db(self):
        """Initialize MongoDB connection and ensure the collection exists."""
        try:
            if not self.mongo_uri:
                raise ValueError("MongoDB URI not found. Ensure MONGO_URL is set in your .env file.")

            self.db_client = AsyncIOMotorClient(self.mongo_uri, serverSelectionTimeoutMS=5000)
            # Test the connection
            await self.db_client.server_info()
            self.db = self.db_client[self.database_name]
            self.afk_collection = self.db[self.collection_name]
            print("MongoDB connected and collection initialized for afk cog.")
        except Exception as e:
            print(f"Failed to connect to MongoDB: {e}")
            raise

    async def get_afk_status(self, user_id):
        """Get the AFK status from the cache or MongoDB."""
        if user_id in self._cache:
            reason, timestamp = self._cache[user_id]
            if datetime.utcnow() - timestamp > self.cache_expiry_duration:
                del self._cache[user_id]  # Expired cache
                return None
            return reason, timestamp

        try:
            result = await self.afk_collection.find_one({"user_id": user_id})
            if result:
                reason = result["reason"]
                timestamp = datetime.fromisoformat(result["timestamp"])
                self._cache[user_id] = (reason, timestamp)  # Update cache
                return reason, timestamp
        except Exception as e:
            print(f"Error fetching AFK status for {user_id}: {e}")
        return None

    async def set_afk_status(self, user_id, reason):
        """Set the AFK status in MongoDB and cache."""
        timestamp = datetime.utcnow().isoformat()
        try:
            await self.afk_collection.update_one(
                {"user_id": user_id},
                {"$set": {"reason": reason, "timestamp": timestamp}},
                upsert=True,
            )
            self._cache[user_id] = (reason, datetime.utcnow())
        except Exception as e:
            print(f"Error setting AFK status for {user_id}: {e}")

    async def remove_afk_status(self, user_id):
        """Remove AFK status from MongoDB and cache."""
        try:
            await self.afk_collection.delete_one({"user_id": user_id})
            self._cache.pop(user_id, None)
        except Exception as e:
            print(f"Error removing AFK status for {user_id}: {e}")

    @commands.command()
    async def afk(self, ctx, *, reason: str = "AFK"):
        """Set yourself as AFK with an optional reason."""
        reason = reason[:100]  # Limit reason to 100 characters
        reason = discord.utils.escape_markdown(reason)  # Escape markdown for safety
        user_id = ctx.author.id
        await self.set_afk_status(user_id, reason)

        embed = discord.Embed(
            description=f"<:sukoon_info:1323251063910043659> | Successfully set your AFK status with reason: {reason}",
            color=0x2f3136,
        )
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handle incoming messages to check or clear AFK statuses."""
        if message.author.bot:
            return

        ctx = await self.bot.get_context(message)
        if ctx.valid:
            return  # Skip processing if it's a command

        try:
            result = await self.get_afk_status(message.author.id)
            if result:
                reason, timestamp = result
                await self.remove_afk_status(message.author.id)

                time_diff = datetime.utcnow() - timestamp
                time_ago = self.format_time_ago(time_diff)

                embed = discord.Embed(
                    description=f"<:sukoon_info:1323251063910043659> | Successfully removed your AFK. You were AFK for {time_ago}.",
                    color=0x2f3136,
                )
                await message.channel.send(embed=embed)

            if message.mentions:
                for mention in message.mentions:
                    result = await self.get_afk_status(mention.id)
                    if result:
                        reason, timestamp = result
                        time_diff = datetime.utcnow() - timestamp
                        time_ago = self.format_time_ago(time_diff)

                        embed = discord.Embed(
                            description=f"<:sukoon_info:1323251063910043659> | {mention.mention} went AFK {time_ago} with reason: {reason}.",
                            color=0x2f3136,
                        )
                        await message.channel.send(embed=embed)
        except Exception as e:
            print(f"Error processing on_message event: {e}")

        await self.bot.process_commands(message)

    @commands.command()
    async def afk_status(self, ctx, member: discord.Member = None):
        """Check the AFK status of yourself or another member."""
        member = member or ctx.author
        try:
            result = await self.get_afk_status(member.id)

            if result:
                reason, timestamp = result
                time_diff = datetime.utcnow() - timestamp
                time_ago = self.format_time_ago(time_diff)

                embed = discord.Embed(
                    description=f"<:sukoon_info:1323251063910043659> {member.mention} is AFK. Reason: {reason}. AFK since: {time_ago}.",
                    color=0x2f3136,
                )
                await ctx.send(embed=embed)
            else:
                embed = discord.Embed(
                    description=f"<a:sukoon_reddot:1322894157794119732> | {member.mention} is not AFK.",
                    color=0x2f3136,
                )
                await ctx.send(embed=embed)
        except Exception as e:
            print(f"Error fetching AFK status for {member.id}: {e}")

    def format_time_ago(self, time_diff):
        """Format a timedelta object into a human-readable 'time ago' string."""
        seconds = time_diff.total_seconds()
        minutes = seconds // 60
        hours = minutes // 60
        days = hours // 24

        if days > 0:
            return f"{int(days)} days ago"
        elif hours > 0:
            return f"{int(hours)} hours ago"
        elif minutes > 0:
            return f"{int(minutes)} minutes ago"
        else:
            return f"{int(seconds)} seconds ago"

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        """Automatically remove AFK status if a user leaves the server."""
        try:
            await self.remove_afk_status(member.id)
        except Exception as e:
            print(f"Error removing AFK status for {member.id} on member remove: {e}")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Automatically remove AFK status when a user joins the server."""
        try:
            await self.remove_afk_status(member.id)
        except Exception as e:
            print(f"Error removing AFK status for {member.id} on member join: {e}")

    @tasks.loop(hours=1)
    async def clean_cache(self):
        """Periodically clean expired cache entries."""
        now = datetime.utcnow()
        expired_keys = [key for key, (_, timestamp) in self._cache.items() if now - timestamp > self.cache_expiry_duration]
        for key in expired_keys:
            del self._cache[key]

    async def cog_unload(self):
        """Close the MongoDB client and clean up tasks."""
        try:
            if self.db_client:
                self.db_client.close()
            self.clean_cache.cancel()
        except Exception as e:
            print(f"Error during cog unload: {e}")

# Setup function to add the cog
async def setup(bot):
    cog = AFK(bot)
    await cog.init_db()
    cog.clean_cache.start()
    await bot.add_cog(cog)
