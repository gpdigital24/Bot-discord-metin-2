"""Same-process dashboard. Access is issued privately by the /panel command."""
import logging
import secrets
import sqlite3
import time
from pathlib import Path

import discord
from aiohttp import web

log = logging.getLogger('dashboard')
STATIC = Path(__file__).with_name('web')


class Dashboard:
    def __init__(self, bot, origin):
        self.bot, self.origin = bot, origin.rstrip('/')
        self.tickets, self.sessions = {}, {}
        self.runner = None
        self.app = web.Application(client_max_size=8192, middlewares=[self.guard])
        self.app.add_routes([
            web.get('/', self.index), web.get('/inviter', self.index), web.get('/app.js', self.script), web.get('/style.css', self.style),
            web.get('/health', self.health), web.post('/api/login', self.login),
            web.get('/api/state', self.state), web.post('/api/logout', self.logout),
            web.post('/api/settings', self.settings), web.post('/api/accounts', self.accounts),
        ])

    def prune(self):
        now = time.time()
        for mapping in (self.tickets, self.sessions):
            for key in list(mapping):
                if mapping[key]['expires'] <= now:
                    del mapping[key]

    def issue(self, guild_id, user_id):
        self.prune()
        for token in list(self.tickets):
            if self.tickets[token]['user_id'] == user_id and self.tickets[token]['guild_id'] == guild_id:
                del self.tickets[token]
        token = secrets.token_urlsafe(32)
        self.tickets[token] = dict(guild_id=guild_id, user_id=user_id, expires=time.time() + 300)
        return f'{self.origin}/#ticket={token}'

    async def member(self, session):
        if not self.bot.is_ready():
            raise web.HTTPServiceUnavailable(text='Le bot se reconnecte. Réessaie dans quelques secondes.')
        guild = self.bot.get_guild(session['guild_id'])
        if guild is None:
            raise web.HTTPForbidden(text='Le bot ne fait plus partie de ce serveur.')
        try:
            member = await guild.fetch_member(session['user_id'])
        except discord.NotFound:
            raise web.HTTPForbidden(text='Tu ne fais plus partie du serveur.') from None
        if not member.guild_permissions.manage_guild:
            raise web.HTTPForbidden(text='La permission Gérer le serveur est nécessaire.')
        return guild, member

    @web.middleware
    async def guard(self, request, handler):
        self.prune()
        try:
            if request.path.startswith('/api/'):
                if request.method == 'POST' and (
                    request.headers.get('Origin') != self.origin
                    or request.content_type != 'application/json'
                ):
                    raise web.HTTPForbidden(text='Requête non autorisée.')
                if request.path != '/api/login':
                    session = self.sessions.get(request.cookies.get('drop_session', ''))
                    if session is None:
                        raise web.HTTPUnauthorized(text='Ouvre un nouveau lien avec /panel dans Discord.')
                    expected_guild = request.headers.get('X-Guild-ID')
                    if expected_guild and expected_guild != str(session['guild_id']):
                        raise web.HTTPForbidden(text='Un autre serveur a été ouvert dans ce navigateur. Relance /panel dans le serveur souhaité.')
                    if request.method == 'POST' and not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), session['csrf']):
                        raise web.HTTPForbidden(text='Recharge le tableau de bord avant de réessayer.')
                    request['session'] = session
                    request['guild'], request['member'] = await self.member(session)
            response = await handler(request)
        except web.HTTPException as error:
            response = web.json_response({'error': error.text}, status=error.status)
        except (ValueError, TypeError, KeyError):
            response = web.json_response({'error': 'Données invalides.'}, status=400)
        except sqlite3.IntegrityError:
            response = web.json_response({'error': 'Un compte porte déjà ce nom.'}, status=409)
        except discord.HTTPException:
            log.exception('Discord indisponible pour le tableau de bord')
            response = web.json_response({'error': 'Discord est indisponible ou les permissions sont insuffisantes.'}, status=503)
        response.headers.update({
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        })
        return response

    async def index(self, request):
        return web.Response(text=(STATIC / 'index.html').read_text(encoding='utf-8'), content_type='text/html')

    async def script(self, request):
        return web.Response(text=(STATIC / 'app.js').read_text(encoding='utf-8'), content_type='application/javascript')

    async def style(self, request):
        return web.Response(text=(STATIC / 'style.css').read_text(encoding='utf-8'), content_type='text/css')

    async def health(self, request):
        return web.json_response({'ready': self.bot.is_ready()}, status=200 if self.bot.is_ready() else 503)

    async def login(self, request):
        data = await request.json()
        ticket = self.tickets.pop(str(data.get('ticket', '')), None)
        if ticket is None:
            raise web.HTTPUnauthorized(text='Ce lien a expiré ou a déjà été utilisé. Relance /panel dans Discord.')
        await self.member(ticket)
        # Replace this browser's old session; tickets are one-use and never logged.
        self.sessions.pop(request.cookies.get('drop_session', ''), None)
        key = secrets.token_urlsafe(32)
        self.sessions[key] = dict(ticket, expires=time.time() + 8 * 3600, csrf=secrets.token_urlsafe(32))
        response = web.json_response({'ok': True})
        response.set_cookie('drop_session', key, httponly=True, secure=self.origin.startswith('https://'), samesite='Strict', max_age=8 * 3600)
        return response

    async def logout(self, request):
        self.sessions.pop(request.cookies.get('drop_session', ''), None)
        response = web.json_response({'ok': True})
        response.del_cookie('drop_session')
        return response

    async def state(self, request):
        guild, member = request['guild'], request['member']
        accounts = []
        saved = self.bot.store.settings(guild.id)
        for row in self.bot.store.all(guild.id):
            item = dict(row)
            item['guild_id'] = str(row['guild_id'])
            owner = guild.get_member(row['owner']) if row['owner'] else None
            if row['owner'] and owner is None:
                try:
                    owner = await guild.fetch_member(row['owner'])
                except discord.NotFound:
                    pass
            item['owner_name'] = owner.display_name if owner else ('Membre inconnu' if row['owner'] else None)
            item['owner'] = str(row['owner']) if row['owner'] else None
            item['message_id'] = str(row['message_id']) if row['message_id'] else None
            item['pending'] = row['id'] in self.bot.dirty
            accounts.append(item)
        return web.json_response({
            'csrf': request['session']['csrf'], 'guild': guild.name, 'guild_id': str(guild.id), 'user': member.display_name,
            'channel_id': str(saved['channel_id']), 'role_id': str(saved['role_id']),
            'channels': [{'id': str(c.id), 'name': c.name} for c in guild.text_channels if c.permissions_for(member).view_channel and c.permissions_for(guild.me).view_channel],
            'roles': [{'id': str(r.id), 'name': r.name} for r in guild.roles if not r.is_default()],
            'accounts': accounts,
        })

    async def settings(self, request):
        data = await request.json()
        guild, member = request['guild'], request['member']
        async with self.bot.lock:
            channel = guild.get_channel(int(data['channel_id']))
            role_id = int(data.get('role_id') or 0)
            role = guild.get_role(role_id) if role_id else None
            if not isinstance(channel, discord.TextChannel) or not channel.permissions_for(member).view_channel:
                raise web.HTTPBadRequest(text='Choisis un salon textuel auquel tu as accès.')
            perms = channel.permissions_for(guild.me)
            if not all((perms.view_channel, perms.send_messages, perms.embed_links, perms.read_message_history)):
                raise web.HTTPBadRequest(text='Le bot doit pouvoir voir le salon, envoyer des messages, intégrer des liens et voir les anciens messages.')
            if role_id and (role is None or role.is_default() or not channel.permissions_for(role).view_channel):
                raise web.HTTPBadRequest(text='Le rôle choisi doit avoir accès au salon.')
            if role and not role.mentionable and not perms.mention_everyone:
                raise web.HTTPBadRequest(text='Rends le rôle mentionnable dans Discord pour recevoir les notifications.')
            previous = self.bot.store.settings(guild.id)['channel_id']
            if previous and previous != channel.id and self.bot.store.all(guild.id):
                raise web.HTTPConflict(text='Retire les comptes existants avant de changer de salon.')
            self.bot.store.configure(guild.id, channel.id, role_id)
        return web.json_response({'message': 'Configuration enregistrée.'})

    async def accounts(self, request):
        data = await request.json()
        action = data['action']
        async with self.bot.lock:
            guild_id = request['guild'].id
            if not self.bot.store.settings(guild_id)['channel_id']:
                raise web.HTTPConflict(text='Enregistre d’abord le salon dans Configuration.')
            if action in ('add', 'rename'):
                name = str(data.get('name', '')).strip()
                if not 1 <= len(name) <= 60:
                    raise web.HTTPBadRequest(text='Le nom doit contenir entre 1 et 60 caractères.')
            if action == 'add':
                account_id = self.bot.store.add(name, guild_id)
            else:
                account_id = int(data['id'])
                account = self.bot.store.get(account_id, guild_id)
                if account is None:
                    raise web.HTTPNotFound(text='Ce compte n’existe plus.')
                if action == 'rename':
                    self.bot.store.rename(account_id, name, guild_id)
                elif action == 'release':
                    if not self.bot.store.release(account_id, request['member'].id, guild_id, manager=True):
                        raise web.HTTPConflict(text='Le compte est déjà disponible.')
                    await self.bot.notify_available(account)
                elif action == 'remove':
                    if account['owner']:
                        raise web.HTTPConflict(text='Libère le compte avant de le retirer.')
                    channel = await self.bot.channel(guild_id)
                    if account['message_id']:
                        try:
                            await channel.get_partial_message(account['message_id']).delete()
                        except discord.NotFound:
                            pass
                    self.bot.store.remove(account_id, guild_id)
                    self.bot.dirty.discard(account_id)
                    return web.json_response({'message': 'Compte retiré.'})
                elif action != 'refresh':
                    raise web.HTTPBadRequest(text='Action inconnue.')
            published = await self.bot.try_publish(account_id)
        return web.json_response({'message': 'Modification enregistrée.' if published else 'Enregistré. La mise à jour Discord sera réessayée automatiquement.'})

    async def start(self, port):
        self.runner = web.AppRunner(self.app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, '0.0.0.0', port).start()
        log.info('Tableau de bord démarré sur le port %s', port)

    async def close(self):
        if self.runner:
            await self.runner.cleanup()
