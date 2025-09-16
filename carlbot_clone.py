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
@