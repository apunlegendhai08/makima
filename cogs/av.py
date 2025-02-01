import discord
from discord.ext import commands, tasks
from discord.ext.commands import Cog
import logging
from datetime import datetime, timedelta
import asyncio

logging.basicConfig(level=logging.INFO)

class AvatarCog(Cog):
    """
    A Discord cog for displaying a user's avatar and banner.
    Includes caching and periodic cleanup to optimize API calls.
    """
    def __init__(self, bot):
        self.bot = bot
        self.cache = {}
        self.cache_expiration = timedelta(minutes=5)
        self.cache_cleanup.start()

    @commands.command(name="av", aliases=["avatar", "profile"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def av(self, ctx, member: discord.Member = None):
        """
        Command to display a user's avatar and banner.
        """
        member = member or ctx.author
        logging.info(f"Fetching avatar and banner for {member} ({member.id})")

        try:
            # Fetch avatar and banner URLs from cache or API
            avatar_url, banner_url = await self.get_user_data(member)

            # Create the avatar embed
            avatar_embed = discord.Embed(
                title=f"{member.display_name}'s Avatar",
                color=discord.Color(0x2f3136),
                timestamp=datetime.utcnow(),
                description=f"[Avatar Link]({avatar_url})"
            )
            avatar_embed.set_image(url=avatar_url or member.default_avatar.url)

            # Display the avatar and optionally a banner button
            if banner_url:
                await self.send_banner_view(ctx, avatar_embed, banner_url, member)
            else:
                avatar_embed.set_footer(text="No banner available.")
                await ctx.send(embed=avatar_embed)

        except asyncio.TimeoutError:
            logging.error(f"Timeout while fetching data for {member} ({member.id}).")
            await ctx.send("The request timed out. Please try again later.")
        except Exception as e:
            logging.error(f"Error displaying avatar or banner: {e}")
            await ctx.send("An error occurred while fetching the avatar or banner. Please try again later.")

    async def send_banner_view(self, ctx, avatar_embed, banner_url, member):
        """ Helper function to handle banner display if available """
        if ctx.guild and ctx.channel.permissions_for(ctx.guild.me).manage_messages:
            class BannerView(discord.ui.View):
                def __init__(self):
                    super().__init__(timeout=180)

                @discord.ui.button(label="Show Banner", style=discord.ButtonStyle.secondary)
                async def show_banner(self, interaction: discord.Interaction, button: discord.ui.Button):
                    banner_embed = discord.Embed(
                        title=f"{member.display_name}'s Banner",
                        color=discord.Color(0x2f3136),
                        timestamp=datetime.utcnow(),
                        description=f"[Banner Link]({banner_url})"
                    )
                    banner_embed.set_image(url=banner_url)
                    await interaction.response.send_message(embed=banner_embed, ephemeral=True)

            await ctx.send(embed=avatar_embed, view=BannerView())
        else:
            avatar_embed.add_field(
                name="Banner Available",
                value="Use this command in a server with bot permissions to view the banner."
            )
            await ctx.send(embed=avatar_embed)

    async def get_user_data(self, member):
        """
        Fetch and cache the avatar and banner URLs for a user.
        """
        # Check cache
        cached_data = self.cache.get(member.id, {})
        current_time = datetime.utcnow()

        # Use cached avatar if valid
        avatar_url = self._get_cached_data(cached_data, "avatar", current_time)
        if not avatar_url:
            avatar_url = str(member.display_avatar)
            self._cache_data(member.id, "avatar", avatar_url, current_time)

        # Use cached banner if valid
        banner_url = self._get_cached_data(cached_data, "banner", current_time)
        if not banner_url:
            try:
                user = await asyncio.wait_for(self.bot.fetch_user(member.id), timeout=10)
                banner_url = str(user.banner.url) if user.banner else None
                self._cache_data(member.id, "banner", banner_url, current_time)
            except asyncio.TimeoutError:
                logging.warning(f"Timeout fetching banner for {member} ({member.id}).")
                banner_url = None
            except Exception as e:
                logging.error(f"Error fetching banner for {member} ({member.id}): {e}")
                banner_url = None

        return avatar_url, banner_url

    def _get_cached_data(self, cached_data, key, current_time):
        """ Helper function to get cached data for avatar/banner """
        if key in cached_data and current_time - cached_data[key]["timestamp"] < self.cache_expiration:
            return cached_data[key]["url"]
        return None

    def _cache_data(self, member_id, key, value, current_time):
        """ Helper function to update the cache """
        if member_id not in self.cache:
            self.cache[member_id] = {}
        self.cache[member_id][key] = {"url": value, "timestamp": current_time}

    @tasks.loop(minutes=1)
    async def cache_cleanup(self):
        """
        Periodically clean up expired cache entries.
        """
        current_time = datetime.utcnow()
        expired_members = [
            member_id
            for member_id, data in self.cache.items()
            if any(
                current_time - entry["timestamp"] > self.cache_expiration
                for entry in data.values()
            )
        ]

        for member_id in expired_members:
            del self.cache[member_id]
            logging.info(f"Removed expired cache for member {member_id}")

    @cache_cleanup.before_loop
    async def before_cache_cleanup(self):
        await self.bot.wait_until_ready()
        logging.info("Starting cache cleanup loop")

async def setup(bot):
    await bot.add_cog(AvatarCog(bot))
