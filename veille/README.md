# Prototype de veille juridique — issue #105

La page **Administration → Veille juridique** permet aux administrateurs métier
(pas uniquement aux superutilisateurs Django) de consulter l'état des sources,
examiner les différences et consigner leurs décisions. Les gestionnaires n'y ont
pas accès. Les décisions et leurs auteurs sont conservés, et leur enregistrement
est inscrit au journal de sécurité.

## Ce que fait cette première version

- Catalogue fixe relu dans `veille/catalogue.py` : fiche Service Public F920 sur
  la rédaction du bail et index général des analyses juridiques ANIL.
- Première collecte = référence initiale, **sans alerte**. Les collectes suivantes
  comparent le texte du bloc HTML `main`, hors scripts et navigation. Les espaces
  sont normalisés ; les modifications éditoriales peuvent donc produire des alertes.
- Une modification crée un instantané et une alerte avec différences déterministes,
  URL, dates des deux collectes et noms de modèles **potentiellement** concernés.
  Une page inchangée ne crée pas de doublon. Un retour à une ancienne version
  crée bien une nouvelle alerte.
- Dernière tentative, dernier succès et dernier échec sont affichés séparément.
  Un échec n'efface pas la référence précédente et la commande sort en erreur
  après avoir essayé les autres sources.
- Examen manuel : à analyser / impact confirmé / sans impact / traitée, avec
  justification obligatoire et historique nominatif. Cet examen ne constitue pas
  une validation juridique d'un modèle et n'en active aucune nouvelle version.
- Aucun modèle importé, bail, mandat ni exemplaire signé n'est modifié.

## Installation et planification

Appliquer les migrations selon la procédure habituelle puis, depuis la racine :

```sh
python manage.py migrate
python manage.py collecter_veille
python manage.py collecter_veille --source bail-service-public
```

La commande n'exécute aucun LLM et n'utilise aucune API payante. Les erreurs et
alertes restent dans l'application ; **aucun e-mail n'est envoyé**.

Pour Docker, lancer manuellement `scripts/veille.sh`. Le script utilise `flock`
(util-linux sur l'hôte) pour éviter les chevauchements sur le même hôte. Le chemin
du verrou peut être adapté avec `VEILLE_VERROU`. Sa configuration n'est pas une
activation : le responsable du déploiement doit ajouter explicitement une tâche
quotidienne dans cron ou le planificateur Synology. Exemple (heure locale de
l'hôte, à vérifier avant installation) :

```cron
0 6 * * * /opt/urbiabail/scripts/veille.sh >> /var/log/urbia-veille.log 2>&1
```

Configurer les droits du fichier journal et sa rotation. Sur plusieurs hôtes,
utiliser un seul ordonnanceur ; le verrou local ne protège pas entre machines.
Les instantanés sont conservés en base uniquement lors d'un changement : les
sauvegardes DB existantes les incluent. Aucune purge automatique n'est prévue
pour ce prototype.

## Limites de couverture et étapes suivantes

Cette surveillance de **deux pages** n'est ni exhaustive ni une garantie de
conformité. L'index ANIL ne couvre pas à lui seul les mandats de gestion. Une mise
à jour d'article derrière un lien de l'index peut ne pas modifier celui-ci et
rester indétectée. Les textes eux-mêmes, la jurisprudence et les arrêtés locaux
ne sont pas collectés. Aucune date d'entrée en vigueur, disposition transitoire,
qualification de projet de texte, catégorie ni territoire n'est déterminé
automatiquement : ces points doivent être consignés dans l'examen humain.

L'API Légifrance/PISTE nécessite une intégration et des accès spécifiques ; elle
n'est **pas intégrée**. Le rattachement au modèle est pour l'instant une piste
fixe du catalogue, pas une analyse du contenu du Word ni un lien à sa version.
La validation initiale des modèles importés (#70), la gestion des validations et
versions de modèles, les avertissements/blocages de génération, les responsables
et échéances d'alertes et les notifications restent hors de cette première version.
Le module suit la mono-agence actuelle et ne fournit pas d'isolation multi-agences.

Les requêtes utilisent uniquement HTTPS vers les domaines exacts du catalogue,
avec le proxy administré de l'hôte s'il est configuré ; les redirections sont
revalidées (3 au maximum).
Le corps est limité à 2 Mo, le timeout de socket à 15 s et la boucle de lecture
à 45 s (une lecture déjà engagée peut encore attendre son timeout).
Une page non HTML, compressée malgré la demande `identity`, illisible, sans bloc
principal ou trop courte est signalée comme échec. Un changement de structure
HTML peut nécessiter une adaptation du collecteur ; une simple réponse 200 n'est
pas une preuve de validité du contenu. Consulter périodiquement les deux sources
et les alertes, même si aucun changement n'apparaît.

## Vérifications reproductibles

```sh
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test veille
```

Les tests réseau sont simulés : ils vérifient extraction, redirections interdites,
limites et erreurs HTTP, baseline, dédoublonnage, retour à une ancienne version,
reprise après panne, droits, échappement HTML, décisions et conservation des
modèles et archives signées. Un contrôle réel se fait séparément avec la commande
ci-dessus ; il n'est pas lancé par la suite de tests.
