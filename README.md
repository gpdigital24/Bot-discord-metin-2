# Bot Discord — Comptes drop

Un panneau par compte, avec prise et libération manuelles. Aucune connexion à Metin2, aucun identifiant de jeu nécessaire.

- Bouton rouge **Prendre le compte** : réserve le compte et affiche le membre et l'heure.
- Bouton vert **Libérer le compte** : accessible à son utilisateur ou aux responsables ayant **Gérer le serveur**.
- Chaque membre ayant accès au salon peut prendre un compte libre.
- Un rôle facultatif est mentionné à la libération. Attribuer ce rôle uniquement aux membres concernés et lui donner accès au salon.
- SQLite conserve les statuts. Les boutons sont réactivés après redémarrage et deux prises simultanées ne peuvent pas réserver le même compte.

## 1. Créer l'application Discord

1. Ouvrir le [portail développeur Discord](https://discord.com/developers/applications), créer une application et son bot.
2. Dans **Bot**, récupérer le token. Le saisir uniquement dans les variables privées Railway, jamais dans un message Discord, un dépôt Git ou le chat.
3. Dans **OAuth2 → URL Generator**, sélectionner `bot` et `applications.commands`.
4. Permissions du bot : **Voir les salons**, **Envoyer des messages**, **Intégrer des liens**, **Voir les anciens messages**. Aucun droit administrateur ni intent privilégié nécessaire.
5. Inviter le bot sur le serveur et lui donner ces permissions dans le salon texte `#compte-drop`.
6. Pour les notifications, créer le rôle `Compte Drop`, l'attribuer aux membres concernés, autoriser ce rôle à voir le salon et rendre le rôle mentionnable. Autre possibilité : autoriser le bot à mentionner tous les rôles uniquement dans ce salon.
7. Activer le **Mode développeur** dans les paramètres avancés Discord. Copier les identifiants du serveur, du salon et du rôle avec le clic droit.

## 2. Déployer sur Railway

1. Mettre ce dossier dans un dépôt GitHub privé. Si le dépôt contient tout le projet Metin2, définir le **Root Directory** du service sur `/discord-bot`. Si ce dossier est seul à la racine du dépôt, laisser la racine par défaut.
2. Sur Railway, créer un projet depuis ce dépôt. Le `Dockerfile` définit la construction et le démarrage.
3. Ajouter un **Volume** au service, monté sur `/data`. Railway renseigne automatiquement `RAILWAY_VOLUME_MOUNT_PATH`. Le bot refuse de démarrer sur Railway sans ce volume pour éviter une sauvegarde éphémère.
4. Ajouter les variables dans l'onglet **Variables** :

| Variable | Valeur |
| --- | --- |
| `DISCORD_TOKEN` | Token privé du bot |
| `DISCORD_GUILD_ID` | ID du serveur |
| `DISCORD_CHANNEL_ID` | ID du salon texte des comptes |
| `DISCORD_NOTIFY_ROLE_ID` | ID du rôle à notifier ; facultatif, omettre pour désactiver |

5. Garder **une seule réplique** et désactiver **Serverless / mise en veille**. Aucun domaine public, port HTTP ou healthcheck HTTP à configurer : le bot se connecte directement à Discord.
6. Déployer puis vérifier le journal : `Bot connecté`. Le service reste actif tant que l'hébergement fonctionne et dispose de crédits.

La base appartient à un seul serveur et salon configurés. Ne pas réutiliser son volume pour un autre serveur/salon sans migration. Activer les sauvegardes du volume dans Railway si souhaité.

## 3. Ajouter les comptes dans Discord

Dans le salon configuré, avec la permission **Gérer le serveur** :

```text
/compte ajouter nom:Drop 1
/compte ajouter nom:Drop 2
/compte renommer numero:1 nom:Drop Forêt
/compte retirer numero:2 confirmer:True
/compte actualiser
```

Le numéro figure au bas de chaque panneau. La suppression exige que le compte soit libre. `actualiser` recrée aussi les panneaux supprimés. Les commandes sont limitées au serveur configuré et leurs permissions sont contrôlées à chaque utilisation.

Les mentions intégrées aux panneaux affichent le joueur sans notification à chaque modification. Une notification au rôle est envoyée uniquement lorsqu'un compte est libéré. Les préférences personnelles de notification Discord peuvent empêcher une alerte sonore/push.

Si Discord refuse une mise à jour, le statut reste sauvegardé et la publication est réessayée toutes les deux minutes. Une notification échouée n'est pas renvoyée automatiquement afin d'éviter des doublons.

## Test local (PowerShell, Python 3.11+)

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m unittest discover -s tests -v
$env:DISCORD_GUILD_ID = 'ID_SERVEUR'
$env:DISCORD_CHANNEL_ID = 'ID_SALON'
$secret = Read-Host 'Token Discord' -AsSecureString
$env:DISCORD_TOKEN = [System.Net.NetworkCredential]::new('', $secret).Password
.\.venv\Scripts\python bot.py
```

Le fichier `.env.example` sert de modèle ; le programme lit les variables d'environnement, sans chargement automatique de `.env`.

Après déploiement : ajouter deux comptes, réserver avec un membre, tenter une seconde prise et une libération avec un autre membre, puis redémarrer le service et vérifier les boutons et le statut sauvegardé.

Documentation : [discord.py](https://discordpy.readthedocs.io/en/stable/interactions/api.html), [volumes Railway](https://docs.railway.com/volumes), [Dockerfiles Railway](https://docs.railway.com/builds/dockerfiles).
