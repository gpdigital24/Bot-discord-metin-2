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
7. Le salon et le rôle seront choisis directement dans Discord avec **/configurer** : aucun identifiant à copier.

## 2. Déployer sur Railway

1. Mettre ce dossier dans un dépôt GitHub privé. Si le dépôt contient tout le projet Metin2, définir le **Root Directory** du service sur `/discord-bot`. Si ce dossier est seul à la racine du dépôt, laisser la racine par défaut.
2. Sur Railway, créer un projet depuis ce dépôt. Le `Dockerfile` définit la construction et le démarrage.
3. Ajouter un **Volume** au service, monté sur `/data`. Railway renseigne automatiquement `RAILWAY_VOLUME_MOUNT_PATH`. Le bot refuse de démarrer sur Railway sans ce volume pour éviter une sauvegarde éphémère.
4. Ajouter les variables dans l'onglet **Variables** :

| Variable | Valeur |
| --- | --- |
| `DISCORD_TOKEN` | Token privé du bot |

5. Garder **une seule réplique** et désactiver **Serverless / mise en veille**. Pour le tableau de bord, dans **Settings → Networking**, générer un domaine Railway pointant sur le port `8080` (ou la variable `PORT` si définie). Railway renseigne `RAILWAY_PUBLIC_DOMAIN`. Relancer le déploiement si nécessaire pour prendre en compte le domaine.
6. Déployer puis vérifier le journal : `Bot connecté`. Le service reste actif tant que l'hébergement fonctionne et dispose de crédits.

Le bot peut être installé sur plusieurs serveurs. Chaque serveur possède son salon, son rôle et ses comptes indépendants. Un même nom de compte peut être utilisé dans deux serveurs. Activer les sauvegardes du volume dans Railway si souhaité.

Lors de la première mise à jour depuis la version à un seul serveur, une copie SQLite `accounts.sqlite3.before-multiguild.bak` est créée sur le volume. La migration conserve les comptes, leurs statuts, leurs boutons et la configuration dans leur serveur d'origine. Si des anciens comptes existent sans serveur identifiable, le bot s'arrête sans modifier leur table ; renseigner alors `DISCORD_GUILD_ID` pour identifier leur serveur.

## Panneau de configuration Discord

### Tableau de bord web

Lancer **/panel** dans Discord avec la permission **Gérer le serveur**. Le bot fournit en privé un lien utilisable une fois, pendant cinq minutes. Le tableau de bord permet de choisir le salon et le rôle, d'ajouter, renommer, retirer les comptes libres, et de libérer un compte en tant que responsable. Les réservations restent dans Discord.

Le lien ouvre uniquement le serveur dans lequel `/panel` a été lancé. Pour gérer un autre serveur, lancer `/panel` dans cet autre serveur. Le nom du serveur est affiché en haut de la page. Une session de navigateur est active à la fois : ouvrir un autre serveur remplace la session précédente. Les anciens onglets bloquent les modifications au lieu de changer silencieusement de serveur.

Les liens sont personnels : ne pas les partager. Les droits Discord sont revérifiés à chaque requête. Une session expire après huit heures ou au redémarrage du bot ; relancer `/panel` pour se reconnecter. Les cookies sont HttpOnly et Secure sur le domaine HTTPS, et les modifications vérifient l'origine et un jeton CSRF. Aucun token Discord n'est transmis au navigateur.

`DASHBOARD_URL` peut remplacer le domaine automatique (URL HTTPS sans chemin). Pour le développement local uniquement, utiliser `http://localhost:8080`. Le serveur web utilise le port `PORT`, 8080 par défaut. `/health` confirme que le bot est connecté.

### Configuration sans navigateur

Une fois le bot connecté, un responsable avec **Gérer le serveur** lance **/configurer** dans Discord. Un panneau privé permet de choisir le salon textuel et un rôle facultatif à notifier, puis d'enregistrer. Le bot vérifie ses permissions dans le salon et la possibilité de mentionner le rôle. Le serveur est détecté automatiquement et la configuration est sauvegardée sur le volume.

Pour modifier le rôle ou désactiver les notifications, relancer la commande. Le panneau expire après cinq minutes. Pour changer de salon alors que des comptes existent, les libérer puis les retirer avant de configurer le nouveau salon. Les commandes globales peuvent mettre un peu de temps à apparaître dans Discord.

Les anciennes variables `DISCORD_GUILD_ID`, `DISCORD_CHANNEL_ID` et `DISCORD_NOTIFY_ROLE_ID` restent facultativement prises en charge pour initialiser ce serveur s'il n'est pas encore configuré ; elles ne limitent pas l'accès aux autres serveurs.

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
$secret = Read-Host 'Token Discord' -AsSecureString
$env:DISCORD_TOKEN = [System.Net.NetworkCredential]::new('', $secret).Password
.\.venv\Scripts\python bot.py
```

Le fichier `.env.example` sert de modèle ; le programme lit les variables d'environnement, sans chargement automatique de `.env`.

Après déploiement : ajouter deux comptes, réserver avec un membre, tenter une seconde prise et une libération avec un autre membre, puis redémarrer le service et vérifier les boutons et le statut sauvegardé.

Documentation : [discord.py](https://discordpy.readthedocs.io/en/stable/interactions/api.html), [volumes Railway](https://docs.railway.com/volumes), [Dockerfiles Railway](https://docs.railway.com/builds/dockerfiles).

## Page publique d’invitation

Partagez https://bot-discord-metin-2-production.up.railway.app/inviter pour inviter le bot sur un serveur. Cette page reste publique même si une session de gestion est ouverte. Le bouton ouvre Discord avec les scopes bot et applications.commands et les seules permissions de salon nécessaires. Après installation, utilisez /panel dans le serveur choisi.

Les notifications acceptent jusqu’à 25 rôles par serveur : cochez-les dans Configuration du panel web, ou sélectionnez-les avec /configurer. Le rôle précédemment enregistré est conservé. Décochez tous les rôles pour désactiver les notifications.

Les rôles sélectionnés sont mentionnés quand un membre prend un compte (avec son nom dans le message) et quand le compte est libéré. Un clic refusé sur un compte déjà occupé ne déclenche aucune notification.
