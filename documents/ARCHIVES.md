# Contrats : droits, signatures et archives

Cette version traite les issues #90 et #91. Elle nécessite la migration
`documents/0002_versioncontrat_pdfarchive_and_more` au déploiement.

## Droits

| Action | Compte sans rôle | Gestionnaire | Administrateur / superutilisateur |
|---|---|---|---|
| Consulter les dossiers et leurs archives | Refusé | Autorisé | Autorisé |
| Préparer une version à transmettre, contrat en cours non signé | Refusé | Autorisé | Autorisé |
| Enregistrer la première signature d'une version préparée | Refusé | Autorisé | Autorisé |
| Changer directement la date d'une signature existante | Refusé | Refusé | Refusé |
| Déposer / remplacer un scan sans changer la date | Refusé | Autorisé, avec conservation des précédents | Autorisé, avec conservation des précédents |
| Retirer une signature | Refusé | Selon les règles métier existantes | Selon les mêmes règles métier |
| Corriger la rédaction signée | Refusé | Refusé | Motif obligatoire, nouvelle archive |
| Mettre fin à un contrat | Refusé | Autorisé | Autorisé |
| Corriger une fin déjà enregistrée | Refusé | Refusé | Motif obligatoire, nouvelle archive |
| Reconstituer un contrat historique signé sans archive | Refusé | Refusé | Reprise explicitement signalée |
| Supprimer un bail signé ou archivé | Refusé | Refusé | Refusé |

Un compte seulement `is_staff`, sans groupe métier, n'a pas d'accès métier.
Les comptes existants doivent appartenir au groupe Gestionnaires ou Administrateurs ;
aucune attribution automatique n'est réalisée lors de la migration.
Les espaces publics candidats et les parcours d'authentification sont conservés.

## Parcours de signature

1. Compléter le contrat et son référentiel.
2. Cliquer **Préparer la version à signer** sur le dossier.
3. Télécharger le PDF archivé et transmettre cette version aux parties.
4. Enregistrer la date de signature et, s'il est disponible, le scan.

La préparation capture les données de fusion, le modèle HTML en service et la
page HTML du document rempli (logo inclus), dans les champs `modele` et `docx`.
Les téléchargements suivants utilisent ces octets. Les archives préparées avant
le passage des modèles au HTML gardent leur Word : il reste téléchargeable et
son PDF passe par LibreOffice.
Le premier PDF obtenu est conservé ; les copies préparation/signature/scan
utilisent le même PDF. Les autres documents, avis et quittances, gardent leur
fonctionnement actuel. La signature est refusée si les données ont changé depuis
la préparation : refaire la préparation et transmettre la nouvelle version.
Un changement de modèle ou de logo ne réécrit pas une préparation existante.

Les archives ne prouvent pas qu'un utilisateur a réellement envoyé le fichier,
ni l'identité des signataires. Le scan reste l'exemplaire signé ; le document
préparé et une correction administrative ne sont pas présentés comme ce scan.

Une correction de rédaction crée une archive séparée, avec auteur, date et motif.
Une correction de fin conserve les octets du document précédent. L'historique
Django conserve également les anciennes valeurs des fiches. Retirer une signature
conserve les archives et retire le scan du contrat redevenu brouillon pour éviter
sa réutilisation lors d'une autre signature.

Les accès à l'administration Django ne permettent pas de modifier directement
les contrats signés ou archivés, ni de restaurer une ancienne fiche par-dessus eux.
Les opérations de signature, retrait, correction et fin sont transactionnelles
et verrouillent la ligne du contrat sous PostgreSQL.

## Contrats déjà signés avant la migration

La migration ne fabrique aucune archive rétroactive. Le scan existant reste
consultable. Le téléchargement d'un PDF généré est bloqué tant qu'il n'existe
pas d'archive : un administrateur peut utiliser **Archiver une reconstitution
actuelle**. Celle-ci capture l'état actuel, porte la mention « original historique
inconnu » et ne prétend pas retrouver l'original. Une correction ou un dépôt tardif
de scan conserve aussi cette distinction.

## Exploitation et validation

Les archives (documents, modèles, scans et PDF) sont stockées en base, ce qui les inclut
dans le dump PostgreSQL. Les fichiers courants continuent de nécessiter la
sauvegarde du volume des documents. Prévoir la croissance de la base, les scans
pouvant atteindre 20 Mo. Ne pas supprimer les archives par SQL ou scripts de purge.
L'immutabilité est appliquée par les parcours applicatifs ; il ne s'agit pas d'un
stockage probatoire certifié ou d'une protection contre un administrateur de base.

Les tests couvrent les requêtes directes par rôle/état, la conservation des octets
et données, les corrections motivées, les scans successifs, la reprise historique,
le cache PDF et le rollback d'une signature si l'archivage échoue. La conversion PDF
est simulée dans ces tests ; la CI du dépôt vérifie séparément Gotenberg et PostgreSQL.
