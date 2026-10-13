import asyncio
import logging
import os
import sqlite3
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import tasks

from store import Store

log = logging.getLogger("drop")


def panel(account):
    occupied = account["owner"] is not None
    description = (
        f"Utilisé par <@{account['owner']}>\nDepuis <t:{account['since']}:f> (<t:{account['since']}:R>)."
        if occupied else "Le compte est disponible. Clique sur **Prendre le compte** pour le réserver."
    )
    embed = discord.Embed(
        title=f"{'🔴' if occupied else '🟢'} {account['name']} — {'OCCUPÉ' if occupied else 'DISPONIBLE'}",
        description=description,
        color=discord.Color.red() if occupied else discord.Color.green(),
    )
    embed.set_footer(text=f"Compte n°{account['id']} • Statut déclaré par les membres")
    return embed


class AccountView(discord.ui.View):
    def __init__(self, bot, occupied=None):
        super().__init__(timeout=None)
        self.bot = bot
        if occupied is not None:
            self.take.disabled = occupied
            self.release.disabled = not occupied

    @discord.ui.button(label="Prendre le compte", style=discord.ButtonStyle.danger, custom_id="drop:take:v1")
    async def take(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.bot.change(interaction, taking=True)

    @discord.ui.button(label="Libérer le compte", style=discord.ButtonStyle.success, custom_id="drop:release:v1")
    async def release(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.bot.change(interaction, taking=False)

    async def on_error(self, interaction, error, item):
        log.error("Échec d'un bouton", exc_info=(type(error), error, error.__traceback__))
        text = "Une erreur est survenue. Vérifie le panneau avant de réessayer."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


class DropBot(discord.Client):
    def __init__(self, guild_id, channel_id, role_id, store):
        super().__init__(intents=discord.Intents.default(), allowed_mentions=discord.AllowedMentions.none())
        self.guild_id, self.channel_id, self.role_id = guild_id, channel_id, role_id
        self.store = store
        self.tree = app_commands.CommandTree(self)
        self.lock = asyncio.Lock()
        self.dirty = {row["id"] for row in store.all()}

    async def setup_hook(self):
        self.add_view(AccountView(self))
        self.tree.add_command(AccountCommands(self), guild=discord.Object(id=self.guild_id))
        await self.tree.sync(guild=discord.Object(id=self.guild_id))
        self.reconcile.start()

    async def on_ready(self):
        log.info("Bot connecté : %s", self.user)

    async def channel(self):
        channel = self.get_channel(self.channel_id) or await self.fetch_channel(self.channel_id)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != self.guild_id:
            raise RuntimeError("DISCORD_CHANNEL_ID doit désigner un salon textuel du serveur configuré.")
        return channel

    def permitted(self, interaction):
        return (
            interaction.guild_id == self.guild_id
            and interaction.channel_id == self.channel_id
            and isinstance(interaction.user, discord.Member)
            and interaction.permissions.view_channel
        )

    async def publish(self, account_id):
        account = self.store.get(account_id)
        if account is None:
            self.dirty.discard(account_id)
            return
        channel = await self.channel()
        view = AccountView(self, occupied=account["owner"] is not None)
        if account["message_id"]:
            try:
                await channel.get_partial_message(account["message_id"]).edit(embed=panel(account), view=view)
                self.dirty.discard(account_id)
                return
            except discord.NotFound:
                pass
        message = await channel.send(embed=panel(account), view=view)
        self.store.message(account_id, message.id)
        self.dirty.discard(account_id)

    async def try_publish(self, account_id):
        self.dirty.add(account_id)
        try:
            await self.publish(account_id)
            return True
        except (discord.HTTPException, RuntimeError):
            log.exception("Panneau %s non mis à jour ; nouvelle tentative automatique", account_id)
            return False

    @tasks.loop(minutes=2)
    async def reconcile(self):
        async with self.lock:
            for account_id in list(self.dirty):
                await self.try_publish(account_id)

    @reconcile.before_loop
    async def before_reconcile(self):
        await self.wait_until_ready()

    async def change(self, interaction, taking):
        if not self.permitted(interaction):
            await interaction.response.send_message("Ce bouton est réservé au salon des comptes drop.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.lock:
            account = self.store.by_message(interaction.message.id)
            if account is None:
                await interaction.followup.send("Ce panneau n'est plus actif.", ephemeral=True)
                return
            manager = interaction.permissions.manage_guild
            changed = (self.store.take(account["id"], interaction.user.id) if taking else
                       self.store.release(account["id"], interaction.user.id, manager))
            if not changed:
                text = ("Ce compte est déjà pris." if taking else
                        "Seuls son utilisateur et les responsables peuvent libérer un compte occupé.")
                await interaction.followup.send(text, ephemeral=True)
                return
            published = await self.try_publish(account["id"])
            result = "Compte réservé pour toi." if taking else "Compte libéré."
            if not published:
                result += " Le statut est sauvegardé ; la mise à jour du panneau sera réessayée."
            await interaction.followup.send(result, ephemeral=True)
            if not taking and self.role_id:
                try:
                    channel = await self.channel()
                    role = channel.guild.get_role(self.role_id)
                    if role is None or role.is_default() or not channel.permissions_for(role).view_channel:
                        log.warning("Rôle de notification absent ou sans accès au salon")
                        return
                    await channel.send(
                        f"{role.mention} — **{discord.utils.escape_markdown(account['name'])}** est disponible !",
                        allowed_mentions=discord.AllowedMentions(roles=[role], users=False, everyone=False),
                    )
                except discord.HTTPException:
                    log.exception("Statut sauvegardé, mais notification non envoyée")

    async def close(self):
        self.reconcile.cancel()
        await super().close()
        self.store.close()


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class AccountCommands(app_commands.Group):
    def __init__(self, bot):
        super().__init__(name="compte", description="Gérer les comptes drop de la guilde")
        self.bot = bot

    async def interaction_check(self, interaction):
        if not self.bot.permitted(interaction) or not interaction.permissions.manage_guild:
            await interaction.response.send_message(
                "Commande réservée aux responsables avec « Gérer le serveur », dans le salon des comptes drop.",
                ephemeral=True,
            )
            return False
        return True

    async def on_error(self, interaction, error):
        log.error("Échec de commande", exc_info=(type(error), error, error.__traceback__))
        text = "Impossible de terminer. Vérifie les permissions du bot et réessaie."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(description="Ajouter un compte et publier ses boutons")
    async def ajouter(self, interaction: discord.Interaction, nom: app_commands.Range[str, 1, 60]):
        await interaction.response.defer(ephemeral=True, thinking=True)
        nom = nom.strip()
        if not nom:
            await interaction.followup.send("Le nom ne peut pas être vide.", ephemeral=True)
            return
        async with self.bot.lock:
            try:
                account_id = self.bot.store.add(nom)
            except sqlite3.IntegrityError:
                await interaction.followup.send("Un compte porte déjà ce nom.", ephemeral=True)
                return
            ok = await self.bot.try_publish(account_id)
        await interaction.followup.send(
            f"Compte n°{account_id} ajouté." + ("" if ok else " Publication en attente : vérifie les permissions du bot."),
            ephemeral=True,
        )

    @app_commands.command(description="Renommer un compte à partir de son numéro")
    async def renommer(self, interaction: discord.Interaction, numero: int, nom: app_commands.Range[str, 1, 60]):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not nom.strip():
            await interaction.followup.send("Le nom ne peut pas être vide.", ephemeral=True)
            return
        async with self.bot.lock:
            try:
                changed = self.bot.store.rename(numero, nom.strip())
            except sqlite3.IntegrityError:
                await interaction.followup.send("Un compte porte déjà ce nom.", ephemeral=True)
                return
            if changed:
                await self.bot.try_publish(numero)
        await interaction.followup.send("Nom sauvegardé." if changed else "Compte introuvable.", ephemeral=True)

    @app_commands.command(description="Retirer un compte libre (confirmation obligatoire)")
    async def retirer(self, interaction: discord.Interaction, numero: int, confirmer: bool):
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.bot.lock:
            account = self.bot.store.get(numero)
            if not confirmer or account is None or account["owner"] is not None:
                await interaction.followup.send("Choisis un compte libre et confirme sa suppression.", ephemeral=True)
                return
            if account["message_id"]:
                channel = await self.bot.channel()
                try:
                    await channel.get_partial_message(account["message_id"]).delete()
                except discord.NotFound:
                    pass
            self.bot.store.remove(numero)
            self.bot.dirty.discard(numero)
        await interaction.followup.send("Compte retiré.", ephemeral=True)

    @app_commands.command(description="Actualiser les panneaux et recréer ceux qui ont été supprimés")
    async def actualiser(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        failures = 0
        async with self.bot.lock:
            for account in self.bot.store.all():
                if not await self.bot.try_publish(account["id"]):
                    failures += 1
        await interaction.followup.send(
            "Panneaux actualisés." if not failures else f"{failures} panneau(x) en attente. Vérifie les permissions du bot.",
            ephemeral=True,
        )


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    required = ("DISCORD_TOKEN", "DISCORD_GUILD_ID", "DISCORD_CHANNEL_ID")
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise SystemExit("Variables manquantes : " + ", ".join(missing))
    try:
        guild_id = int(os.environ["DISCORD_GUILD_ID"])
        channel_id = int(os.environ["DISCORD_CHANNEL_ID"])
        role_id = int(os.getenv("DISCORD_NOTIFY_ROLE_ID") or "0")
    except ValueError:
        raise SystemExit("Les identifiants Discord doivent être des nombres.") from None
    if os.getenv("RAILWAY_ENVIRONMENT_ID") and not os.getenv("RAILWAY_VOLUME_MOUNT_PATH"):
        raise SystemExit("Ajoute un volume Railway monté sur /data pour conserver les statuts.")
    directory = os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or os.getenv("DATA_DIR", "data")
    store = Store(Path(directory) / "accounts.sqlite3")
    DropBot(guild_id, channel_id, role_id, store).run(os.environ["DISCORD_TOKEN"], log_handler=None)


if __name__ == "__main__":
    main()
