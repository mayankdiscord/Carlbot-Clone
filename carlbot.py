import discord
from discord.ext import commands, tasks
import json
import asyncio
import re
import datetime
from typing import Optional, Union
import aiohttp
import random

# Bot configuration
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# Data storage (in production, use a proper database)
guild_configs = {}
automod_configs = {}
reaction_roles = {}
user_warnings = {}
muted_users = {}
automod_violations = {}

def load_guild_config(guild_id):
    """Load or create guild configuration"""
    if guild_id not in guild_configs:
        guild_configs[guild_id] = {
            'prefix': '!',
            'log_channel': None,
            'mute_role': None,
            'welcome_channel': None,
            'welcome_message': None,
            'leave_channel': None,
            'leave_message': None,
            'autoroles': []
        }
    return guild_configs[guild_id]

def load_automod_config(guild_id):
    """Load or create automod configuration"""
    if guild_id not in automod_configs:
        automod_configs[guild_id] = {
            'enabled': False,
            'anti_spam': False,
            'anti_raid': False,
            'filter_words': [],
            'filter_links': False,
            'filter_invites': False,
            'max_mentions': 5,
            'max_emojis': 10,
            'punishment': 'warn'  # warn, mute, kick, ban
        }
    return automod_configs[guild_id]

@bot.event
async def on_ready():
    print(f'{bot.user} has logged in!')
    print(f'Bot is in {len(bot.guilds)} guilds')
    automod_check.start()

@bot.event
async def on_guild_join(guild):
    """Initialize configuration when bot joins a server"""
    load_guild_config(guild.id)
    load_automod_config(guild.id)

@bot.event
async def on_member_join(member):
    """Handle member join events"""
    config = load_guild_config(member.guild.id)
    
    # Auto roles
    for role_id in config['autoroles']:
        try:
            role = member.guild.get_role(role_id)
            if role:
                await member.add_roles(role)
        except:
            pass
    
    # Welcome message
    if config['welcome_channel'] and config['welcome_message']:
        channel = bot.get_channel(config['welcome_channel'])
        if channel:
            message = config['welcome_message']
            message = message.replace('{user}', member.mention)
            message = message.replace('{server}', member.guild.name)
            message = message.replace('{membercount}', str(member.guild.member_count))
            await channel.send(message)

@bot.event
async def on_member_remove(member):
    """Handle member leave events"""
    config = load_guild_config(member.guild.id)
    
    if config['leave_channel'] and config['leave_message']:
        channel = bot.get_channel(config['leave_channel'])
        if channel:
            message = config['leave_message']
            message = message.replace('{user}', str(member))
            message = message.replace('{server}', member.guild.name)
            message = message.replace('{membercount}', str(member.guild.member_count))
            await channel.send(message)

# MODERATION COMMANDS
@bot.command(name='kick')
@commands.has_permissions(kick_members=True)
async def kick_member(ctx, member: discord.Member, *, reason="No reason provided"):
    """Kick a member from the server"""
    try:
        await member.kick(reason=reason)
        embed = discord.Embed(
            title="Member Kicked",
            description=f"{member.mention} has been kicked.\nReason: {reason}",
            color=0xff9500
        )
        await ctx.send(embed=embed)
        await log_action(ctx.guild, f"**{member}** was kicked by **{ctx.author}**\nReason: {reason}")
    except Exception as e:
        await ctx.send(f"Failed to kick member: {e}")

@bot.command(name='ban')
@commands.has_permissions(ban_members=True)
async def ban_member(ctx, member: Union[discord.Member, discord.User], *, reason="No reason provided"):
    """Ban a member from the server"""
    try:
        await ctx.guild.ban(member, reason=reason)
        embed = discord.Embed(
            title="Member Banned",
            description=f"{member.mention} has been banned.\nReason: {reason}",
            color=0xff0000
        )
        await ctx.send(embed=embed)
        await log_action(ctx.guild, f"**{member}** was banned by **{ctx.author}**\nReason: {reason}")
    except Exception as e:
        await ctx.send(f"Failed to ban member: {e}")

@bot.command(name='unban')
@commands.has_permissions(ban_members=True)
async def unban_member(ctx, user_id: int, *, reason="No reason provided"):
    """Unban a user from the server"""
    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user, reason=reason)
        embed = discord.Embed(
            title="Member Unbanned",
            description=f"{user} has been unbanned.\nReason: {reason}",
            color=0x00ff00
        )
        await ctx.send(embed=embed)
        await log_action(ctx.guild, f"**{user}** was unbanned by **{ctx.author}**\nReason: {reason}")
    except Exception as e:
        await ctx.send(f"Failed to unban user: {e}")

@bot.command(name='mute')
@commands.has_permissions(manage_roles=True)
async def mute_member(ctx, member: discord.Member, duration: Optional[str] = None, *, reason="No reason provided"):
    """Mute a member"""
    config = load_guild_config(ctx.guild.id)
    
    # Create mute role if it doesn't exist
    mute_role = None
    if config['mute_role']:
        mute_role = ctx.guild.get_role(config['mute_role'])
    
    if not mute_role:
        mute_role = await create_mute_role(ctx.guild)
        config['mute_role'] = mute_role.id
    
    try:
        await member.add_roles(mute_role, reason=reason)
        
        # Parse duration
        unmute_time = None
        if duration:
            unmute_time = parse_duration(duration)
            if unmute_time:
                muted_users[member.id] = {
                    'guild_id': ctx.guild.id,
                    'unmute_time': unmute_time,
                    'role_id': mute_role.id
                }
        
        embed = discord.Embed(
            title="Member Muted",
            description=f"{member.mention} has been muted.\nDuration: {duration or 'Permanent'}\nReason: {reason}",
            color=0xffff00
        )
        await ctx.send(embed=embed)
        await log_action(ctx.guild, f"**{member}** was muted by **{ctx.author}**\nDuration: {duration or 'Permanent'}\nReason: {reason}")
    except Exception as e:
        await ctx.send(f"Failed to mute member: {e}")

@bot.command(name='unmute')
@commands.has_permissions(manage_roles=True)
async def unmute_member(ctx, member: discord.Member, *, reason="No reason provided"):
    """Unmute a member"""
    config = load_guild_config(ctx.guild.id)
    
    if config['mute_role']:
        mute_role = ctx.guild.get_role(config['mute_role'])
        if mute_role and mute_role in member.roles:
            try:
                await member.remove_roles(mute_role, reason=reason)
                if member.id in muted_users:
                    del muted_users[member.id]
                
                embed = discord.Embed(
                    title="Member Unmuted",
                    description=f"{member.mention} has been unmuted.\nReason: {reason}",
                    color=0x00ff00
                )
                await ctx.send(embed=embed)
                await log_action(ctx.guild, f"**{member}** was unmuted by **{ctx.author}**\nReason: {reason}")
            except Exception as e:
                await ctx.send(f"Failed to unmute member: {e}")
        else:
            await ctx.send("Member is not muted.")

@bot.command(name='warn')
@commands.has_permissions(manage_messages=True)
async def warn_member(ctx, member: discord.Member, *, reason="No reason provided"):
    """Warn a member"""
    if member.id not in user_warnings:
        user_warnings[member.id] = []
    
    warning = {
        'guild_id': ctx.guild.id,
        'reason': reason,
        'moderator': ctx.author.id,
        'timestamp': datetime.datetime.now().isoformat()
    }
    
    user_warnings[member.id].append(warning)
    
    embed = discord.Embed(
        title="Member Warned",
        description=f"{member.mention} has been warned.\nReason: {reason}\nTotal warnings: {len(user_warnings[member.id])}",
        color=0xffa500
    )
    await ctx.send(embed=embed)
    await log_action(ctx.guild, f"**{member}** was warned by **{ctx.author}**\nReason: {reason}")

@bot.command(name='warnings')
async def show_warnings(ctx, member: Optional[discord.Member] = None):
    """Show warnings for a member"""
    if not member:
        member = ctx.author
    
    if member.id not in user_warnings:
        await ctx.send(f"{member.mention} has no warnings.")
        return
    
    warnings = [w for w in user_warnings[member.id] if w['guild_id'] == ctx.guild.id]
    
    if not warnings:
        await ctx.send(f"{member.mention} has no warnings in this server.")
        return
    
    embed = discord.Embed(
        title=f"Warnings for {member}",
        color=0xffa500
    )
    
    for i, warning in enumerate(warnings[-10:], 1):  # Show last 10 warnings
        moderator = bot.get_user(warning['moderator'])
        embed.add_field(
            name=f"Warning {i}",
            value=f"**Reason:** {warning['reason']}\n**Moderator:** {moderator or 'Unknown'}\n**Date:** {warning['timestamp'][:10]}",
            inline=False
        )
    
    await ctx.send(embed=embed)

@bot.command(name='clear', aliases=['purge'])
@commands.has_permissions(manage_messages=True)
async def clear_messages(ctx, amount: int = 10):
    """Clear messages from the channel"""
    if amount > 100:
        amount = 100
    
    deleted = await ctx.channel.purge(limit=amount + 1)
    embed = discord.Embed(
        title="Messages Cleared",
        description=f"Deleted {len(deleted) - 1} messages.",
        color=0x00ff00
    )
    
    msg = await ctx.send(embed=embed)
    await asyncio.sleep(5)
    await msg.delete()

# UTILITY COMMANDS
@bot.command(name='userinfo', aliases=['ui'])
async def user_info(ctx, member: Optional[discord.Member] = None):
    """Get information about a user"""
    if not member:
        member = ctx.author
    
    embed = discord.Embed(
        title=f"User Info - {member}",
        color=member.color
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID", value=member.id, inline=True)
    embed.add_field(name="Nickname", value=member.nick or "None", inline=True)
    embed.add_field(name="Status", value=str(member.status).title(), inline=True)
    embed.add_field(name="Account Created", value=member.created_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
    embed.add_field(name="Joined Server", value=member.joined_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
    embed.add_field(name="Roles", value=", ".join([role.mention for role in member.roles[1:]]) or "None", inline=False)
    
    await ctx.send(embed=embed)

@bot.command(name='serverinfo', aliases=['si'])
async def server_info(ctx):
    """Get information about the server"""
    guild = ctx.guild
    
    embed = discord.Embed(
        title=f"Server Info - {guild.name}",
        color=0x00ff00
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    
    embed.add_field(name="Owner", value=guild.owner.mention, inline=True)
    embed.add_field(name="Members", value=guild.member_count, inline=True)
    embed.add_field(name="Channels", value=len(guild.channels), inline=True)
    embed.add_field(name="Roles", value=len(guild.roles), inline=True)
    embed.add_field(name="Emojis", value=len(guild.emojis), inline=True)
    embed.add_field(name="Created", value=guild.created_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
    embed.add_field(name="Verification Level", value=str(guild.verification_level).title(), inline=True)
    embed.add_field(name="Boost Level", value=f"Level {guild.premium_tier}", inline=True)
    embed.add_field(name="Boost Count", value=guild.premium_subscription_count, inline=True)
    
    await ctx.send(embed=embed)

@bot.command(name='avatar', aliases=['av'])
async def get_avatar(ctx, member: Optional[discord.Member] = None):
    """Get a user's avatar"""
    if not member:
        member = ctx.author
    
    embed = discord.Embed(
        title=f"{member}'s Avatar",
        color=member.color
    )
    embed.set_image(url=member.display_avatar.url)
    
    await ctx.send(embed=embed)

# AUTOMOD SYSTEM
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
    # Check automod
    if message.guild:
        await check_automod(message)
    
    await bot.process_commands(message)

async def check_automod(message):
    """Check message against automod rules"""
    config = load_automod_config(message.guild.id)
    
    if not config['enabled']:
        return
    
    violations = []
    
    # Check filtered words
    if config['filter_words']:
        for word in config['filter_words']:
            if word.lower() in message.content.lower():
                violations.append(f"Filtered word: {word}")
    
    # Check invite links
    if config['filter_invites']:
        invite_pattern = r'discord\.gg/\w+|discordapp\.com/invite/\w+'
        if re.search(invite_pattern, message.content, re.IGNORECASE):
            violations.append("Discord invite link")
    
    # Check external links
    if config['filter_links']:
        url_pattern = r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
        if re.search(url_pattern, message.content):
            violations.append("External link")
    
    # Check excessive mentions
    mentions = len(message.mentions) + len(message.role_mentions)
    if mentions > config['max_mentions']:
        violations.append(f"Too many mentions ({mentions})")
    
    # Check excessive emojis
    emoji_count = len(re.findall(r'<:\w*:\d*>', message.content))
    if emoji_count > config['max_emojis']:
        violations.append(f"Too many emojis ({emoji_count})")
    
    # Apply punishment if violations found
    if violations:
        await delete_message_and_punish(message, violations, config['punishment'])

async def delete_message_and_punish(message, violations, punishment):
    """Delete message and apply punishment"""
    try:
        await message.delete()
        
        # Log violation
        violation_text = ", ".join(violations)
        await log_action(message.guild, f"**AutoMod:** Deleted message from **{message.author}** in {message.channel.mention}\nViolations: {violation_text}")
        
        # Apply punishment
        if punishment == 'warn':
            if message.author.id not in user_warnings:
                user_warnings[message.author.id] = []
            
            warning = {
                'guild_id': message.guild.id,
                'reason': f"AutoMod violation: {violation_text}",
                'moderator': bot.user.id,
                'timestamp': datetime.datetime.now().isoformat()
            }
            user_warnings[message.author.id].append(warning)
            
        elif punishment == 'mute':
            config = load_guild_config(message.guild.id)
            if config['mute_role']:
                mute_role = message.guild.get_role(config['mute_role'])
                if mute_role:
                    await message.author.add_roles(mute_role, reason=f"AutoMod violation: {violation_text}")
        
        # Send notification to user
        try:
            embed = discord.Embed(
                title="AutoMod Violation",
                description=f"Your message in **{message.guild.name}** was deleted for violating server rules.\nViolations: {violation_text}",
                color=0xff0000
            )
            await message.author.send(embed=embed)
        except:
            pass  # User has DMs disabled
            
    except:
        pass  # Message might already be deleted

# REACTION ROLES
@bot.command(name='reactionrole', aliases=['rr'])
@commands.has_permissions(manage_roles=True)
async def reaction_role(ctx, message_id: int, emoji, role: discord.Role):
    """Add a reaction role to a message"""
    try:
        message = await ctx.channel.fetch_message(message_id)
        await message.add_reaction(emoji)
        
        if message_id not in reaction_roles:
            reaction_roles[message_id] = {}
        
        reaction_roles[message_id][str(emoji)] = role.id
        
        embed = discord.Embed(
            title="Reaction Role Added",
            description=f"React with {emoji} to get the {role.mention} role!",
            color=0x00ff00
        )
        await ctx.send(embed=embed)
        
    except Exception as e:
        await ctx.send(f"Failed to add reaction role: {e}")

@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return
    
    message_id = reaction.message.id
    if message_id in reaction_roles:
        emoji_str = str(reaction.emoji)
        if emoji_str in reaction_roles[message_id]:
            role_id = reaction_roles[message_id][emoji_str]
            role = reaction.message.guild.get_role(role_id)
            if role:
                try:
                    await user.add_roles(role)
                except:
                    pass

@bot.event
async def on_reaction_remove(reaction, user):
    if user.bot:
        return
    
    message_id = reaction.message.id
    if message_id in reaction_roles:
        emoji_str = str(reaction.emoji)
        if emoji_str in reaction_roles[message_id]:
            role_id = reaction_roles[message_id][emoji_str]
            role = reaction.message.guild.get_role(role_id)
            if role:
                try:
                    await user.remove_roles(role)
                except:
                    pass

# CONFIGURATION COMMANDS
@bot.group(name='config')
@commands.has_permissions(administrator=True)
async def config(ctx):
    """Server configuration commands"""
    if ctx.invoked_subcommand is None:
        embed = discord.Embed(
            title="Configuration Commands",
            description="`!config prefix <prefix>` - Set bot prefix\n"
                       "`!config log <channel>` - Set log channel\n"
                       "`!config welcome <channel> <message>` - Set welcome settings\n"
                       "`!config leave <channel> <message>` - Set leave settings\n"
                       "`!config autorole <role>` - Add autorole",
            color=0x00ff00
        )
        await ctx.send(embed=embed)

@config.command(name='prefix')
async def set_prefix(ctx, prefix):
    """Set the bot prefix for this server"""
    config = load_guild_config(ctx.guild.id)
    config['prefix'] = prefix
    
    embed = discord.Embed(
        title="Prefix Updated",
        description=f"Bot prefix has been changed to `{prefix}`",
        color=0x00ff00
    )
    await ctx.send(embed=embed)

@config.command(name='log')
async def set_log_channel(ctx, channel: discord.TextChannel):
    """Set the log channel"""
    config = load_guild_config(ctx.guild.id)
    config['log_channel'] = channel.id
    
    embed = discord.Embed(
        title="Log Channel Set",
        description=f"Log channel has been set to {channel.mention}",
        color=0x00ff00
    )
    await ctx.send(embed=embed)

@config.command(name='welcome')
async def set_welcome(ctx, channel: discord.TextChannel, *, message):
    """Set welcome message and channel"""
    config = load_guild_config(ctx.guild.id)
    config['welcome_channel'] = channel.id
    config['welcome_message'] = message
    
    embed = discord.Embed(
        title="Welcome Settings Updated",
        description=f"Welcome messages will be sent to {channel.mention}\n\n**Message:** {message}",
        color=0x00ff00
    )
    await ctx.send(embed=embed)

# AUTOMOD CONFIGURATION
@bot.group(name='automod')
@commands.has_permissions(administrator=True)
async def automod(ctx):
    """AutoMod configuration commands"""
    if ctx.invoked_subcommand is None:
        config = load_automod_config(ctx.guild.id)
        embed = discord.Embed(
            title="AutoMod Settings",
            description=f"**Status:** {'Enabled' if config['enabled'] else 'Disabled'}\n"
                       f"**Filter Words:** {len(config['filter_words'])} words\n"
                       f"**Filter Links:** {'Yes' if config['filter_links'] else 'No'}\n"
                       f"**Filter Invites:** {'Yes' if config['filter_invites'] else 'No'}\n"
                       f"**Max Mentions:** {config['max_mentions']}\n"
                       f"**Max Emojis:** {config['max_emojis']}\n"
                       f"**Punishment:** {config['punishment'].title()}",
            color=0x00ff00
        )
        await ctx.send(embed=embed)

@automod.command(name='enable')
async def enable_automod(ctx):
    """Enable AutoMod"""
    config = load_automod_config(ctx.guild.id)
    config['enabled'] = True
    
    embed = discord.Embed(
        title="AutoMod Enabled",
        description="AutoMod has been enabled for this server.",
        color=0x00ff00
    )
    await ctx.send(embed=embed)

@automod.command(name='disable')
async def disable_automod(ctx):
    """Disable AutoMod"""
    config = load_automod_config(ctx.guild.id)
    config['enabled'] = False
    
    embed = discord.Embed(
        title="AutoMod Disabled",
        description="AutoMod has been disabled for this server.",
        color=0xff0000
    )
    await ctx.send(embed=embed)

@automod.command(name='addword')
async def add_filtered_word(ctx, *, word):
    """Add a word to the filter"""
    config = load_automod_config(ctx.guild.id)
    if word.lower() not in config['filter_words']:
        config['filter_words'].append(word.lower())
        await ctx.send(f"Added `{word}` to the word filter.")
    else:
        await ctx.send(f"`{word}` is already in the word filter.")

@automod.command(name='removeword')
async def remove_filtered_word(ctx, *, word):
    """Remove a word from the filter"""
    config = load_automod_config(ctx.guild.id)
    if word.lower() in config['filter_words']:
        config['filter_words'].remove(word.lower())
        await ctx.send(f"Removed `{word}` from the word filter.")
    else:
        await ctx.send(f"`{word}` is not in the word filter.")

# UTILITY FUNCTIONS
async def create_mute_role(guild):
    """Create a mute role with proper permissions"""
    try:
        mute_role = await guild.create_role(
            name="Muted",
            color=discord.Color(0x818386),
            reason="Auto-created mute role"
        )
        
        for channel in guild.channels:
            try:
                await channel.set_permissions(mute_role, send_messages=False, speak=False, add_reactions=False)
            except:
                pass
        
        return mute_role
    except:
        return None

def parse_duration(duration_str):
    """Parse duration string (e.g., '1h', '30m', '1d')"""
    duration_regex = re.match(r'(\d+)([smhd])', duration_str.lower())
    if not duration_regex:
        return None
    
    amount = int(duration_regex.group(1))
    unit = duration_regex.group(2)
    
    if unit == 's':
        seconds = amount
    elif unit == 'm':
        seconds = amount * 60
    elif unit == 'h':
        seconds = amount * 3600
    elif unit == 'd':
        seconds = amount * 86400
    else:
        return None
    
    return datetime.datetime.now() + datetime.timedelta(seconds=seconds)

async def log_action(guild, message):
    """Log an action to the log channel"""
    config = load_guild_config(guild.id)
    if config['log_channel']:
        channel = guild.get_channel(config['log_channel'])
        if channel:
            embed = discord.Embed(
                description=message,
                timestamp=datetime.datetime.now(),
                color=0x00ff00
            )
            try:
                await channel.send(embed=embed)
            except:
                pass

@tasks.loop(minutes=1)
async def automod_check():
    """Check for expired mutes"""
    current_time = datetime.datetime.now()
    to_unmute = []
    
    for user_id, mute_data in muted_users.items():
        if mute_data['unmute_time'] <= current_time:
            to_unmute.append(user_id)
    
    for user_id in to_unmute:
        mute_data = muted_users[user_id]
        guild = bot.get_guild(mute_data['guild_id'])
        if guild:
            member = guild.get_member(user_id)
            role = guild.get_role(mute_data['role_id'])
            if member and role:
                try:
                    await member.remove_roles(role, reason="Mute expired")
                    await log_action(guild, f"**{member}** was automatically unmuted (mute expired)")
                except:
                    pass
        
        del muted_users[user_id]

# FUN COMMANDS
@bot.command(name='8ball')
async def eight_ball(ctx, *, question):
    """Ask the magic 8-ball a question"""
    responses = [
        "It is certain.", "It is decidedly so.", "Without a doubt.", "Yes definitely.",
        "You may rely on it.", "As I see it, yes.", "Most likely.", "Outlook good.",
        "Yes.", "Signs point to yes.", "Reply hazy, try again.", "Ask again later.",
        "Better not tell you now.", "Cannot predict now.", "Concentrate and ask again.",
        "Don't count on it.", "My reply is no.", "My sources say no.",
        "Outlook not so good.", "Very doubtful."
    ]
    
    embed = discord.Embed(
        title="🎱 Magic 8-Ball",
        description=f"**Question:** {question}\n**Answer:** {random.choice(responses)}",
        color=0x000000
    )
    await ctx.send(embed=embed)

@bot.command(name='roll')
async def roll_dice(ctx, dice: str = "1d6"):
    """Roll dice (e.g., 1d6, 2d20)"""
    try:
        rolls, sides = map(int, dice.split('d'))
        if rolls > 20 or sides > 100:
            await ctx.send("Too many dice or sides! Maximum 20d100.")
            return
        
        results = [random.randint(1, sides) for _ in range(rolls)]
        total = sum(results)
        
        embed = discord.Embed(
            title="🎲 Dice Roll",
            description=f"**Dice:** {dice}\n**Results:** {', '.join(map(str, results))}\n**Total:** {total}",
            color=0x00ff00
        )
        await ctx.send(embed=embed)
        
    except ValueError:
        await ctx.send("Invalid dice format! Use format like `1d6` or `2d20`.")

# HELP COMMAND
@bot.command(name='help')
async def help_command(ctx, category: str = None):
    """Custom help command"""
    if not category:
        embed = discord.Embed(
            title="📚 Bot Commands Help",
            description="Use `!help <category>` for detailed information about a category.\n\n"
                       "**Categories:**\n"
                       "🔨 `moderation` - Moderation commands\n"
                       "⚙️ `config` - Server configuration\n"
                       "🤖 `automod` - AutoMod settings\n"
                       "🎭 `roles` - Reaction roles\n"
                       "ℹ️ `utility` - Utility commands\n"
                       "🎉 `fun` - Fun commands",
            color=0x00ff00
        )
        await ctx.send(embed=embed)
        return
    
    category = category.lower()
    
    if category == "moderation":
        embed = discord.Embed(
            title="🔨 Moderation Commands",
            description="**!kick <user> [reason]** - Kick a member\n"
                       "**!ban <user> [reason]** - Ban a member\n"
                       "**!unban <user_id> [reason]** - Unban a user\n"
                       "**!mute <user> [duration] [reason]** - Mute a member\n"
                       "**!unmute <user> [reason]** - Unmute a member\n"
                       "**!warn <user> [reason]** - Warn a member\n"
                       "**!warnings [user]** - Show warnings\n"
                       "**!clear [amount]** - Clear messages",
            color=0xff9500
        )
    elif category == "config":
        embed = discord.Embed(
            title="⚙️ Configuration Commands",
            description="**!config prefix <prefix>** - Set bot prefix\n"
                       "**!config log <channel>** - Set log channel\n"
                       "**!config welcome <channel> <message>** - Set welcome message\n"
                       "**!config leave <channel> <message>** - Set leave message\n"
                       "**!config autorole <role>** - Add autorole",
            color=0x0099ff
        )
    elif category == "automod":
        embed = discord.Embed(
            title="🤖 AutoMod Commands",
            description="**!automod** - Show current settings\n"
                       "**!automod enable** - Enable AutoMod\n"
                       "**!automod disable** - Disable AutoMod\n"
                       "**!automod addword <word>** - Add filtered word\n"
                       "**!automod removeword <word>** - Remove filtered word",
            color=0xff0080
        )
    elif category == "roles":
        embed = discord.Embed(
            title="🎭 Reaction Role Commands",
            description="**!reactionrole <message_id> <emoji> <role>** - Add reaction role\n"
                       "React to messages to get/remove roles automatically!",
            color=0x8000ff
        )
    elif category == "utility":
        embed = discord.Embed(
            title="ℹ️ Utility Commands",
            description="**!userinfo [user]** - Get user information\n"
                       "**!serverinfo** - Get server information\n"
                       "**!avatar [user]** - Get user's avatar\n"
                       "**!ping** - Check bot latency",
            color=0x00ff80
        )
    elif category == "fun":
        embed = discord.Embed(
            title="🎉 Fun Commands",
            description="**!8ball <question>** - Ask the magic 8-ball\n"
                       "**!roll [dice]** - Roll dice (e.g., 1d6, 2d20)",
            color=0xffff00
        )
    else:
        embed = discord.Embed(
            title="❌ Unknown Category",
            description="Available categories: moderation, config, automod, roles, utility, fun",
            color=0xff0000
        )
    
    await ctx.send(embed=embed)

@bot.command(name='ping')
async def ping(ctx):
    """Check bot latency"""
    embed = discord.Embed(
        title="🏓 Pong!",
        description=f"Latency: {round(bot.latency * 1000)}ms",
        color=0x00ff00
    )
    await ctx.send(embed=embed)

# LEVELING SYSTEM (Simple implementation)
user_xp = {}

@bot.event
async def on_message_xp(message):
    """Award XP for messages (call this from on_message)"""
    if message.author.bot or not message.guild:
        return
    
    user_id = message.author.id
    guild_id = message.guild.id
    
    if guild_id not in user_xp:
        user_xp[guild_id] = {}
    
    if user_id not in user_xp[guild_id]:
        user_xp[guild_id][user_id] = {'xp': 0, 'level': 1}
    
    # Award 1-3 XP per message (random)
    xp_gain = random.randint(1, 3)
    user_xp[guild_id][user_id]['xp'] += xp_gain
    
    # Check for level up
    current_xp = user_xp[guild_id][user_id]['xp']
    current_level = user_xp[guild_id][user_id]['level']
    xp_needed = current_level * 100  # 100 XP per level
    
    if current_xp >= xp_needed:
        user_xp[guild_id][user_id]['level'] += 1
        user_xp[guild_id][user_id]['xp'] = current_xp - xp_needed
        
        embed = discord.Embed(
            title="🎉 Level Up!",
            description=f"{message.author.mention} reached level {user_xp[guild_id][user_id]['level']}!",
            color=0x00ff00
        )
        await message.channel.send(embed=embed)

@bot.command(name='level', aliases=['lvl'])
async def check_level(ctx, member: Optional[discord.Member] = None):
    """Check user level and XP"""
    if not member:
        member = ctx.author
    
    guild_id = ctx.guild.id
    user_id = member.id
    
    if guild_id not in user_xp or user_id not in user_xp[guild_id]:
        embed = discord.Embed(
            title="📊 Level Information",
            description=f"{member.mention} hasn't gained any XP yet!",
            color=0xff0000
        )
        await ctx.send(embed=embed)
        return
    
    user_data = user_xp[guild_id][user_id]
    level = user_data['level']
    xp = user_data['xp']
    xp_needed = level * 100
    
    embed = discord.Embed(
        title="📊 Level Information",
        color=member.color
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="User", value=member.mention, inline=True)
    embed.add_field(name="Level", value=level, inline=True)
    embed.add_field(name="XP", value=f"{xp}/{xp_needed}", inline=True)
    
    await ctx.send(embed=embed)

@bot.command(name='leaderboard', aliases=['lb'])
async def leaderboard(ctx):
    """Show server leaderboard"""
    guild_id = ctx.guild.id
    
    if guild_id not in user_xp or not user_xp[guild_id]:
        embed = discord.Embed(
            title="📊 Leaderboard",
            description="No users have gained XP yet!",
            color=0xff0000
        )
        await ctx.send(embed=embed)
        return
    
    # Sort users by level, then by XP
    sorted_users = sorted(
        user_xp[guild_id].items(),
        key=lambda x: (x[1]['level'], x[1]['xp']),
        reverse=True
    )
    
    embed = discord.Embed(
        title="🏆 Server Leaderboard",
        color=0x00ff00
    )
    
    for i, (user_id, data) in enumerate(sorted_users[:10], 1):
        user = bot.get_user(user_id)
        if user:
            embed.add_field(
                name=f"{i}. {user}",
                value=f"Level {data['level']} ({data['xp']} XP)",
                inline=False
            )
    
    await ctx.send(embed=embed)

# ECONOMY SYSTEM (Simple implementation)
user_economy = {}

def get_user_economy(guild_id, user_id):
    """Get user economy data"""
    if guild_id not in user_economy:
        user_economy[guild_id] = {}
    
    if user_id not in user_economy[guild_id]:
        user_economy[guild_id][user_id] = {
            'coins': 100,  # Starting coins
            'bank': 0,
            'last_daily': None,
            'last_work': None
        }
    
    return user_economy[guild_id][user_id]

@bot.command(name='balance', aliases=['bal'])
async def check_balance(ctx, member: Optional[discord.Member] = None):
    """Check coin balance"""
    if not member:
        member = ctx.author
    
    data = get_user_economy(ctx.guild.id, member.id)
    
    embed = discord.Embed(
        title="💰 Balance",
        color=0x00ff00
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Wallet", value=f"{data['coins']:,} coins", inline=True)
    embed.add_field(name="Bank", value=f"{data['bank']:,} coins", inline=True)
    embed.add_field(name="Total", value=f"{data['coins'] + data['bank']:,} coins", inline=True)
    
    await ctx.send(embed=embed)

@bot.command(name='daily')
async def daily_reward(ctx):
    """Claim daily reward"""
    data = get_user_economy(ctx.guild.id, ctx.author.id)
    now = datetime.datetime.now()
    
    if data['last_daily']:
        last_daily = datetime.datetime.fromisoformat(data['last_daily'])
        if (now - last_daily).days < 1:
            time_left = last_daily + datetime.timedelta(days=1) - now
            hours, remainder = divmod(int(time_left.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)
            
            embed = discord.Embed(
                title="⏰ Daily Cooldown",
                description=f"You can claim your daily reward in {hours}h {minutes}m!",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            return
    
    reward = random.randint(50, 200)
    data['coins'] += reward
    data['last_daily'] = now.isoformat()
    
    embed = discord.Embed(
        title="🎁 Daily Reward",
        description=f"You received {reward} coins!",
        color=0x00ff00
    )
    await ctx.send(embed=embed)

@bot.command(name='work')
async def work_command(ctx):
    """Work for coins"""
    data = get_user_economy(ctx.guild.id, ctx.author.id)
    now = datetime.datetime.now()
    
    if data['last_work']:
        last_work = datetime.datetime.fromisoformat(data['last_work'])
        if (now - last_work).total_seconds() < 3600:  # 1 hour cooldown
            time_left = last_work + datetime.timedelta(hours=1) - now
            minutes, _ = divmod(int(time_left.total_seconds()), 60)
            
            embed = discord.Embed(
                title="⏰ Work Cooldown",
                description=f"You can work again in {minutes} minutes!",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            return
    
    jobs = [
        "coding", "streaming", "gaming", "teaching", "cooking",
        "cleaning", "gardening", "writing", "drawing", "singing"
    ]
    
    job = random.choice(jobs)
    reward = random.randint(20, 80)
    data['coins'] += reward
    data['last_work'] = now.isoformat()
    
    embed = discord.Embed(
        title="💼 Work Complete",
        description=f"You worked as a {job} and earned {reward} coins!",
        color=0x00ff00
    )
    await ctx.send(embed=embed)

@bot.command(name='gamble')
async def gamble_coins(ctx, amount: int):
    """Gamble your coins"""
    if amount <= 0:
        await ctx.send("You need to gamble at least 1 coin!")
        return
    
    data = get_user_economy(ctx.guild.id, ctx.author.id)
    
    if amount > data['coins']:
        await ctx.send("You don't have enough coins!")
        return
    
    # 45% chance to win, 55% chance to lose
    if random.random() < 0.45:
        winnings = int(amount * 1.5)
        data['coins'] += winnings - amount
        
        embed = discord.Embed(
            title="🎰 Jackpot!",
            description=f"You won {winnings} coins! (+{winnings - amount})",
            color=0x00ff00
        )
    else:
        data['coins'] -= amount
        
        embed = discord.Embed(
            title="💸 You Lost!",
            description=f"You lost {amount} coins!",
            color=0xff0000
        )
    
    await ctx.send(embed=embed)

# MUSIC COMMANDS (Basic structure - requires voice support)
@bot.command(name='join')
async def join_voice(ctx):
    """Join voice channel"""
    if ctx.author.voice:
        channel = ctx.author.voice.channel
        await channel.connect()
        
        embed = discord.Embed(
            title="🎵 Joined Voice Channel",
            description=f"Connected to {channel.name}",
            color=0x00ff00
        )
        await ctx.send(embed=embed)
    else:
        await ctx.send("You need to be in a voice channel!")

@bot.command(name='leave')
async def leave_voice(ctx):
    """Leave voice channel"""
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        
        embed = discord.Embed(
            title="🎵 Left Voice Channel",
            description="Disconnected from voice channel",
            color=0xff0000
        )
        await ctx.send(embed=embed)
    else:
        await ctx.send("I'm not in a voice channel!")

# TICKET SYSTEM
ticket_categories = {}

@bot.command(name='ticket')
async def create_ticket(ctx, *, reason="No reason provided"):
    """Create a support ticket"""
    guild = ctx.guild
    category = None
    
    # Find or create ticket category
    if guild.id in ticket_categories:
        category = guild.get_channel(ticket_categories[guild.id])
    
    if not category:
        category = await guild.create_category("🎫 Support Tickets")
        ticket_categories[guild.id] = category.id
    
    # Create ticket channel
    channel_name = f"ticket-{ctx.author.name.lower()}-{ctx.author.discriminator}"
    
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        ctx.author: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    
    # Add admin permissions
    for role in guild.roles:
        if role.permissions.administrator:
            overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    
    try:
        ticket_channel = await guild.create_text_channel(
            channel_name,
            category=category,
            overwrites=overwrites
        )
        
        embed = discord.Embed(
            title="🎫 Support Ticket Created",
            description=f"Ticket: {ticket_channel.mention}\nReason: {reason}",
            color=0x00ff00
        )
        embed.add_field(name="Close Ticket", value="Use `!close` to close this ticket", inline=False)
        
        await ticket_channel.send(f"{ctx.author.mention}", embed=embed)
        await ctx.send(f"Ticket created: {ticket_channel.mention}")
        
    except Exception as e:
        await ctx.send(f"Failed to create ticket: {e}")

@bot.command(name='close')
async def close_ticket(ctx):
    """Close a support ticket"""
    if not ctx.channel.name.startswith("ticket-"):
        await ctx.send("This command can only be used in ticket channels!")
        return
    
    embed = discord.Embed(
        title="🎫 Closing Ticket",
        description="This ticket will be closed in 10 seconds...",
        color=0xff0000
    )
    await ctx.send(embed=embed)
    
    await asyncio.sleep(10)
    await ctx.channel.delete(reason="Ticket closed")

# ERROR HANDLING
@bot.event
async def on_command_error(ctx, error):
    """Handle command errors"""
    if isinstance(error, commands.MissingPermissions):
        embed = discord.Embed(
            title="❌ Missing Permissions",
            description="You don't have permission to use this command!",
            color=0xff0000
        )
        await ctx.send(embed=embed)
    
    elif isinstance(error, commands.MemberNotFound):
        embed = discord.Embed(
            title="❌ Member Not Found",
            description="I couldn't find that member!",
            color=0xff0000
        )
        await ctx.send(embed=embed)
    
    elif isinstance(error, commands.MissingRequiredArgument):
        embed = discord.Embed(
            title="❌ Missing Argument",
            description=f"Missing required argument: `{error.param.name}`\nUse `!help` for command usage.",
            color=0xff0000
        )
        await ctx.send(embed=embed)
    
    elif isinstance(error, commands.CommandNotFound):
        # Silently ignore unknown commands
        pass
    
    else:
        # Log unexpected errors
        print(f"Unexpected error: {error}")
        embed = discord.Embed(
            title="❌ An Error Occurred",
            description="Something went wrong while executing that command.",
            color=0xff0000
        )
        await ctx.send(embed=embed)

# REMINDER SYSTEM
reminders = {}

@bot.command(name='remind', aliases=['remindme'])
async def set_reminder(ctx, duration, *, message):
    """Set a reminder"""
    remind_time = parse_duration(duration)
    if not remind_time:
        await ctx.send("Invalid duration format! Use formats like `1h`, `30m`, `1d`.")
        return
    
    reminder_id = len(reminders)
    reminders[reminder_id] = {
        'user_id': ctx.author.id,
        'channel_id': ctx.channel.id,
        'message': message,
        'time': remind_time
    }
    
    embed = discord.Embed(
        title="⏰ Reminder Set",
        description=f"I'll remind you about: {message}\nTime: {remind_time.strftime('%Y-%m-%d %H:%M:%S')}",
        color=0x00ff00
    )
    await ctx.send(embed=embed)

@tasks.loop(minutes=1)
async def check_reminders():
    """Check for due reminders"""
    current_time = datetime.datetime.now()
    to_remove = []
    
    for reminder_id, reminder in reminders.items():
        if reminder['time'] <= current_time:
            user = bot.get_user(reminder['user_id'])
            channel = bot.get_channel(reminder['channel_id'])
            
            if user and channel:
                embed = discord.Embed(
                    title="⏰ Reminder",
                    description=f"You asked me to remind you about: {reminder['message']}",
                    color=0x00ff00
                )
                try:
                    await channel.send(f"{user.mention}", embed=embed)
                except:
                    pass
            
            to_remove.append(reminder_id)
    
    for reminder_id in to_remove:
        del reminders[reminder_id]

# Start reminder checking when bot is ready
@bot.event
async def on_ready_tasks():
    check_reminders.start()

# ADDITIONAL UTILITY COMMANDS
@bot.command(name='say')
@commands.has_permissions(manage_messages=True)
async def say_message(ctx, *, message):
    """Make the bot say something"""
    await ctx.message.delete()
    await ctx.send(message)

@bot.command(name='embed')
@commands.has_permissions(manage_messages=True)
async def create_embed(ctx, title, *, description):
    """Create an embed message"""
    embed = discord.Embed(
        title=title,
        description=description,
        color=0x00ff00
    )
    await ctx.send(embed=embed)

@bot.command(name='poll')
async def create_poll(ctx, question, *options):
    """Create a poll"""
    if len(options) < 2:
        await ctx.send("You need at least 2 options for a poll!")
        return
    
    if len(options) > 10:
        await ctx.send("Maximum 10 options allowed!")
        return
    
    embed = discord.Embed(
        title="📊 Poll",
        description=question,
        color=0x00ff00
    )
    
    reactions = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']
    
    for i, option in enumerate(options):
        embed.add_field(name=f"{reactions[i]} {option}", value="\u200b", inline=False)
    
    poll_message = await ctx.send(embed=embed)
    
    for i in range(len(options)):
        await poll_message.add_reaction(reactions[i])

# Modified on_message to include XP system
original_on_message = bot.get_listener('on_message')

@bot.event
async def on_message_combined(message):
    if message.author.bot:
        return
    
    # Check automod
    if message.guild:
        await check_automod(message)
        # Award XP
        await on_message_xp(message)
    
    await bot.process_commands(message)

# Replace the on_message event
bot.remove_listener(on_message)
bot.add_listener(on_message_combined, 'on_message')

# BOT TOKEN - Replace with your bot token
# bot.run('MTM5MzU1NTM0ODI4NzM5MzgyMg.GdTnJv.ckKWNKCZ7al-7i6kulNK-om1lD9kqSO2yvjF3c')
