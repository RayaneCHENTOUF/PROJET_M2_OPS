# Qualité des données — du brut aux articles propres

Ce document décrit ce qui pollue le flux brut, les règles qui l'écartent et les contrôles qui vérifient les données après chaque passage. Il est complété au fil des TD (journal des ajustements en fin de fichier).

## 1. Reprise : la collecte a-t-elle tourné ?

Oui. Passages locaux du 12/11/2026 (10h20, 10h27, 10h30), puis toutes les 30 min sous Airflow.

- 1er passage : 7 sources sur 10 en succès. La Voix du Nord, Le Télégramme et Les Échos renvoyaient **HTTP 403** (protection anti-robots). Elles ont été remplacées par L'Est Républicain, Le Dauphiné Libéré et La Tribune (voir `sources/sources.csv`).
- Depuis : **10/10 sources en succès**, aucune en sommeil. La Tribune répond `304` quand son flux n'a pas changé.

Articles bruts au 12/11/2026, 10h30 :

| Source | Type | Articles bruts |
|---|---|---:|
| La Dépêche du Midi | PQR | 100 |
| Libération | PQN | 51 |
| France 24 | PQN | 30 |
| Sud Ouest | PQR | 21 |
| Le Figaro | PQN | 21 |
| Le Dauphiné Libéré | PQR | 20 |
| L'Est Républicain | PQR | 20 |
| La Tribune | PQN | 20 |
| Le Monde | PQN | 19 |
| Ouest-France | PQR | 10 |
| **Total** | | **312** |

Pour refaire le compte : `python -m collect etat` (état des sources) et `ls data/articles_bruts/*/*/ | wc -l`.

## 2. Les dix pollueurs

Repérés en lisant le brut du 12/11/2026. « Observé » signifie qu'un cas réel figure dans nos données. « Attendu » signifie que le cas n'est pas encore apparu dans nos flux, mais qu'il est connu sur ces sites et doit être couvert par une fixture de test.

| # | Pollueur | Statut | Exemple réel ou attendu | Pourquoi c'est un problème |
|---|---|---|---|---|
| 1 | **Contenu repris à l'identique entre titres d'un même groupe ou d'une agence** | Observé : 8 cas | L'Est Républicain **et** Le Dauphiné Libéré (groupe EBRA) : « Info EBRA. Lait, agrumes, volailles : la France se lance à la reconquête de son autonomie alimentaire » | Un même texte compté deux fois gonfle artificiellement un sujet (« couvert par 2 sources » alors qu'il n'y a qu'une rédaction). |
| 2 | **Doublon approximatif** (même événement, titre reformulé à quelques mots près) | Observé : 7 paires (Jaccard ≥ 0,6) | Sud Ouest « VIDÉOS. New York : un immeuble s'effondre à Brooklyn, au moins deux morts… » / Le Figaro « New York : un immeuble s'effondre à Brooklyn, au moins deux morts… » (Jaccard 0,76) | Entre deux sources différentes, c'est **le signal du TD 5** (regroupement) : on ne le rejette pas. Au sein d'une **même source** (article republié avec un titre retouché), c'est un vrai doublon. |
| 3 | **Doublon exact** (même source, même titre, lien différent) | Attendu | Un article republié avec un nouveau paramètre d'URL (`?utm_…`, `?at_medium=…`) | Le dédoublonnage de la collecte se fait sur `(source, lien)` : un lien différent laisse passer le doublon. |
| 4 | **Contenus magazine / lifestyle hors actualité** | Observé : 4 cas | Le Dauphiné, rubrique « Magazine Lifestyle » : « Animaux. Peut-on donner sans risque de la clémentine à son chien ? » ; « Magazine Tourisme et Patrimoine » : « Islande : les plus beaux paysages… » | Contenu intemporel, sans événement : il ne peut pas former un sujet de la gazette. |
| 5 | **Publi-reportage / contenu partenaire / bons plans** | Attendu | « Contenu partenaire », « Sponsorisé », « Bon plan », « Black Friday : -40 % sur… » | Contenu payé par un annonceur : ce n'est pas de l'information et nous n'avons pas à le republier. |
| 6 | **Horoscope** | Attendu | « Horoscope du jeudi 12 novembre 2026 : Bélier… » | Contenu récurrent sans valeur d'actualité, publié tous les jours. |
| 7 | **Programmes TV** | Attendu | « Programme TV : que regarder ce soir à la télé ? » | Idem : contenu de service quotidien. |
| 8 | **Jeux, concours, tirages** | Attendu (et piège observé) | « Résultats du Loto du mercredi 11 novembre », « Mots fléchés », « Jeu-concours : gagnez… » | Contenu de service. **Piège** : le simple mot « concours » attrape de vrais articles (voir § 3). |
| 9 | **Titre vide, date future** | Attendu (0 aujourd'hui) | Item sans `<title>` ; `pubDate` dans le futur (fuseau mal déclaré ou article programmé) | Un titre vide ne peut pas être affiché. Une date future fausse le tri de l'édition et la fraîcheur. |
| 10 | **Encodage cassé, HTML et entités résiduels** | Attendu (0 aujourd'hui) | « Ã©lection » au lieu de « élection », `&amp;`, `<p>…</p>` dans le résumé | Texte illisible à l'affichage, et deux titres identiques qui ne se ressemblent plus pour le dédoublonnage. |

### Constats utiles pour la suite

- **Les préfixes éditoriaux** (« VIDÉOS. », « CARTE. », « REPORTAGE. », « Isère. », « Info EBRA. ») varient d'une source à l'autre pour un même événement. Il faut les retirer **pour comparer** les titres (dédoublonnage), mais les garder dans le titre affiché.
- **139 articles sur 312 n'ont pas de rubrique.** Une règle fondée seulement sur la rubrique ne suffit pas : il faut la compléter par des motifs sur le titre.
- Aujourd'hui, nos flux sont propres sur l'encodage, le HTML et les dates (0 cas). Les règles correspondantes seront donc testées sur une **fixture fabriquée** (`tests/fixtures/`), pas sur les données du jour.

## 3. Faux positifs repérés (règles à ne pas écrire trop larges)

| Motif naïf | Vrai article qu'il écarterait à tort |
|---|---|
| `concours` | Le Dauphiné : « Isère. « Si ce concours fonctionne, on veut que ça reste ici et qu'il ne parte pas à Paris » » |
| `concours` | La Dépêche : « REPORTAGE. Avec son concours de fléchettes, ce pub de Tarbes met dans le mille… » |
| `prix`, `offre` | La Tribune : « Est-on à l'aube d'une baisse massive des prix de l'immobilier ? » ; L'Est Républicain : « … ils offrent 2 500 € pour trouver… » |
| rubrique `Culture - Loisirs` | L'Est Républicain : « Incendie du château de Bayon… » (un vrai fait d'actualité) |

D'où le principe retenu : **des motifs précis** (« jeu-concours », « résultats du loto », « contenu partenaire ») plutôt que des mots isolés, et un rapport qui montre des exemples de titres rejetés par règle, pour qu'on voie tout de suite une règle trop gourmande.

## 4. Normalisation (avant les règles)

`qualite/normalisation.py` : une fonction pure par transformation, chacune testée (`tests/test_normalisation.py`). Elles sont appliquées au titre et au résumé dans cet ordre :

1. `reparer_encodage` : « Ã© » → « é », « â€™ » → « ’ », double encodage compris. La réparation se fait séquence par séquence, et un texte sain n'est jamais modifié.
2. `decoder_entites` : « &amp; » → « & », double échappement compris.
3. `retirer_html` : balises retirées, contenu des `<script>`/`<style>` aussi. « 3 < 5 » est conservé.
4. `normaliser_espaces` : forme NFC, caractères invisibles retirés, espaces (dont insécables) réduites à une seule.
5. `borner_extrait` : 300 caractères au plus, coupés sur un mot, suivis de « … ».
6. `date_utc` : toutes les dates en UTC.
7. `cle_titre` : forme de comparaison (préfixes éditoriaux retirés, minuscules, sans accents ni ponctuation). Elle sert au dédoublonnage et n'est jamais affichée.

Seuls les champs republiables passent dans `articles_propres` : titre, extrait court, lien, dates, source, rubrique. Le `payload` brut reste dans le fichier d'origine.

## 5. Règles d'exclusion

Code : `qualite/regles.py`. Les motifs et seuils sont regroupés dans la section CONFIGURATION en tête du fichier. Tests : `tests/test_regles.py`, sur la fixture `tests/fixtures/articles_pollues.json`. Chaque règle y a des cas à rejeter **et** de vrais articles qui doivent passer.

Les règles s'appliquent dans l'ordre du tableau. **La première qui rejette l'emporte**, et son identifiant est enregistré dans `rejets.regle`, avec un `detail` qui dit précisément quoi (motif trouvé, doublon visé). Les motifs portent sur le texte en minuscules et sans accents.

| Id | Ce qu'elle écarte | Pourquoi | Exemple |
|---|---|---|---|
| `R01_titre_vide` | Titre vide après nettoyage (y compris un titre fait uniquement de HTML) | Impossible à afficher | `<b> </b>&nbsp;` |
| `R02_date_future` | `publie_le` postérieur au traitement de plus d'1 h | Fausse le tri et la fraîcheur. La marge d'1 h absorbe les horloges et fuseaux mal déclarés | Publié le 14/11 alors qu'on est le 12/11 |
| `R03_encodage` | Mojibake que la normalisation n'a pas pu réparer, ou caractère « � » | Texte illisible à l'affichage | « R�forme des retraites » |
| `R04_publi` | Contenu partenaire ou sponsorisé, publi-reportage, bon plan, code promo, vente flash (titre, extrait ou rubrique) | Contenu payé : ce n'est pas de l'information | « CONTENU PARTENAIRE. Rénovation énergétique… » ; « Bon plan : l'aspirateur à moins de 100 € » |
| `R05_horoscope` | Horoscope, astrologie (titre ou rubrique) | Contenu quotidien sans actualité | « Horoscope du jeudi 12 novembre » |
| `R06_programme_tv` | « Programme TV », « Que regarder ce soir » (titre ou rubrique) | Contenu de service quotidien | « Programme TV : que regarder ce soir à la télé ? » |
| `R07_jeux` | Jeu-concours, « places à gagner », résultats et tirages du Loto ou de l'EuroMillions, mots fléchés, sudoku, quiz | Contenu de service. **Pas** le mot « concours » seul (voir § 3) | « Résultats du Loto du mercredi 11 novembre » |
| `R08_magazine` | Rubrique qui commence par « Magazine » | Contenu intemporel, sans événement. Observé chez EBRA | « Animaux. Peut-on donner de la clémentine à son chien ? » (Magazine Lifestyle) |
| `R09_doublon_exact` | Même source, même `cle_titre`, lien différent | Le dédoublonnage de la collecte porte sur `(source, lien)` : un lien modifié (`?utm_…`) laisse passer une republication | Le même titre du Monde avec et sans `?utm_source=rss` |
| `R10_reprise` | Même `cle_titre` chez une **autre** source. On garde la première publiée | Dépêche d'agence ou contenu de groupe reproduit à l'identique : une seule rédaction, compter deux sources serait faux | L'Est Républicain et Le Dauphiné Libéré (EBRA) : « Info EBRA. Lait, agrumes, volailles… » |
| `R11_doublon_approx` | Même source, titre à Jaccard ≥ 0,8 (sur les mots de `cle_titre`) d'un article gardé dans les 48 h. Titres d'au moins 4 mots | Article republié avec un titre retouché (bilan mis à jour, coquille corrigée) | « …au moins deux morts et 17 blessés » puis « …et 18 blessés » |

### Choix assumés

- **Doublon approximatif limité à une même source.** Deux sources différentes qui titrent presque pareil sur le même événement (Libération / Sud Ouest sur l'immeuble effondré à New York, Jaccard 0,87), c'est exactement le signal que le regroupement du TD 5 doit exploiter. Le rejeter ici ferait perdre l'information « couvert par plusieurs rédactions ».
- **Fenêtre de 48 h** pour les doublons : on compare aux articles gardés récemment, pas à toute la base. C'est rapide, et un même titre six mois plus tard (« Résultats du bac ») n'est pas un doublon.
- **Le premier publié est gardé** : les nouveaux articles d'un passage sont traités dans l'ordre de `publie_le`.

### Premier passage sur les vraies données (12/11/2026)

312 articles entrés : **300 gardés, 12 rejetés** (`R08_magazine` 4, `R10_reprise` 8). Le Dauphiné Libéré perd 10 articles sur 20 : 7 reprises de L'Est Républicain (les deux journaux sont du groupe EBRA) et 3 rubriques Magazine.

À surveiller : `R10_reprise` a aussi écarté Le Figaro, dont le titre « New York : un immeuble s'effondre à Brooklyn, au moins deux morts et 17 blessés » est identique, au préfixe « VIDÉOS. » près, à celui de Sud Ouest. C'est probablement le titre de la dépêche AFP repris tel quel par les deux rédactions, ce qui correspond bien à ce que la règle doit écarter. Mais si le TD 5 a besoin de savoir que plusieurs rédactions couvrent un événement, il faudra plutôt **marquer** ces reprises que les rejeter.

## 6. Contrôles de qualité

Un contrôle vérifie **un fait sur les vraies données**, après chaque passage. Ce n'est pas un test : la CI (`tests/test_regles.py`) vérifie le **code** des règles sur une fixture, alors qu'un contrôle vérifie que la table `articles_propres`, telle qu'elle est en base, tient ses promesses.

Code : `qualite/controles.py`. Commande : `python -m qualite controler`. Dans Airflow, c'est la tâche `controler`, qui suit `traiter` :

```
migrer -> sync_sources -> collecter -+-> verifier_sommeil
                                     +-> traiter -> controler
```

### Les trois faits

| Contrôle | Fait vérifié | Requête | Seuil |
|---|---|---|---|
| `date_future` | Aucun article propre publié dans le futur | `publie_le > maintenant + 1 h` sur `articles_propres` | 0 |
| `titre_vide` | Aucun article propre sans titre | `TRIM(titre) = ''` sur `articles_propres` | 0 |
| `taux_rejet` | Le taux de rejet d'une source ne dépasse pas 80 % | `rejets / (propres + rejets)` par source, sur les articles traités dans les **24 dernières heures** | 80 % |

Les deux premiers faits sont censés être garantis par `R01_titre_vide` et `R02_date_future`. Le contrôle sert à **le prouver** sur les données : si quelqu'un affaiblit une règle, ou écrit dans la table par un autre chemin, il le voit. Le troisième fait détecte une règle trop gourmande, ou un flux qui a changé de format (par exemple tous les titres devenus vides).

Deux choix pour `taux_rejet` :
- **une fenêtre de 24 h** plutôt que le seul dernier passage : un passage de 30 min ne ramène que quelques articles par source, et 2 rejets sur 2 feraient 100 % ;
- **un minimum de 5 articles** sur la fenêtre pour juger une source. En dessous, le taux est mesuré et publié, mais il ne déclenche pas d'alerte.

Les seuils sont dans la section CONFIGURATION de `qualite/controles.py`. Le seuil de rejet peut aussi être changé sans toucher au code, avec `--seuil-rejet 0.5` ou la variable `GAZETTE_SEUIL_REJET`.

### Les métriques

À chaque contrôle, les chiffres sont écrits dans la table **`metriques_qualite`** (migration V003), une ligne par mesure `(mesure_le, nom, source, valeur)`. La source vaut `*` pour une valeur globale. Les mêmes lignes apparaissent dans le journal de la tâche (`metrique nom=… source="…" valeur=…`).

| Métrique | Par source | Sens |
|---|---|---|
| `articles_entres`, `articles_gardes`, `articles_rejetes` | oui, plus `*` | Volumes traités sur 24 h |
| `taux_rejet` | oui, plus `*` | Part rejetée sur 24 h |
| `dates_futures`, `titres_vides` | `*` | Nombre de cas trouvés (doit valoir 0) |
| `controle_date_future`, `controle_titre_vide`, `controle_taux_rejet` | `*` | 1 = fait vrai, 0 = fait faux |

Exemple pour suivre le taux de rejet d'une source dans le temps :

```sql
SELECT mesure_le, valeur FROM metriques_qualite
WHERE nom = 'taux_rejet' AND source = 'Le Dauphiné Libéré' ORDER BY mesure_le;
```

### L'alerte

Quand un fait est faux :
1. une ligne `ERROR … ALERTE controle=… valeur=… seuil=… : détail` apparaît dans le journal, avec les sources ou articles en cause ;
2. la commande sort avec le **code 1**, et la tâche Airflow `controler` **passe en rouge**. C'est le même mécanisme que `verifier_sommeil` pour les sources en sommeil ;
3. *optionnel* : si `GAZETTE_ALERTE_WEBHOOK` est défini (dans `.env` ou dans l'environnement de `docker compose`), le message est aussi posté sur ce webhook. Le format est compatible Slack, Teams et Discord. Si l'envoi échoue, l'erreur est journalisée, et la tâche reste rouge de toute façon.

**L'alerte a sonné** le 12/11/2026. Avec le seuil abaissé à 25 % (`GAZETTE_SEUIL_REJET=0.25`), la tâche Airflow `controler` est passée en FAILED :

```
ERROR qualite.controles ALERTE controle=taux_rejet valeur=0.3 seuil=0.25 :
      sources au-dessus de 25% sur 24 h : Le Dauphiné Libéré 30% (6/20)
Marking task as FAILED. dag_id=collecte, task_id=controler
```

Pour la refaire : `docker compose exec -e GAZETTE_SEUIL_REJET=0.25 airflow airflow tasks test collecte controler 2026-11-12`.

Avec le seuil réel de 80 %, le même jour, les trois contrôles sont verts. Le pire taux est celui du Dauphiné Libéré, à 30 %, à cause des reprises EBRA (`R10_reprise`).

### Outil dédié ou assertions maison : assertions maison

Nous avons écarté Great Expectations et Soda pour quatre raisons :
- **trois faits, trois requêtes SQL**. Un outil dédié apporterait surtout de la configuration (contextes, suites, checkpoints) pour un gain nul à cette échelle ;
- **pas de nouvelle dépendance dans l'image Airflow**. Great Expectations tire de nombreuses dépendances qui entrent en conflit avec celles d'Airflow (SQLAlchemy notamment, déjà en 1.4 dans l'image) ;
- **le même code tourne partout** : SQLite en local et dans les tests, MySQL dans Docker ;
- **des contrôles testés comme le reste** : `tests/test_controles.py` vérifie que chaque fait sonne quand il est faux et se tait sinon.

Nous reviendrons sur ce choix si les contrôles se multiplient (plus d'une dizaine de faits, plusieurs tables), ou si l'on a besoin des rapports HTML de Great Expectations.

## 7. Rapport de qualité du jour

Code : `qualite/rapport.py`. Commande : `python -m qualite rapport`. Dans Airflow, c'est la tâche `rapport`, qui suit `controler` :

```
migrer -> sync_sources -> collecter -+-> verifier_sommeil
                                     +-> traiter -> controler -> rapport
```

**Un fichier par jour** : `rapports/qualite-AAAA-MM-JJ.md`, avec une journée comptée en UTC. Il est **réécrit à chaque passage**, soit toutes les 30 min. On lit donc un seul fichier le matin, et les jours précédents restent archivés à côté. Un fichier par passage en aurait produit environ 48 par jour, ce qui est trop pour être lu. L'écriture est atomique (fichier temporaire, puis renommage) : on ne tombe jamais sur un rapport à moitié écrit.

La tâche `rapport` tourne **même si `controler` est rouge** (`trigger_rule=all_done`). C'est justement quand un contrôle sonne qu'on a besoin du rapport pour comprendre pourquoi.

Tout est relu dans la base (`articles_propres`, `rejets`, `metriques_qualite`). On peut donc régénérer n'importe quel jour : `python -m qualite rapport --date 2026-11-12`.

### Contenu

| Section | Ce qu'elle montre |
|---|---|
| En bref | **Entrés, gardés, rejetés** : cumul de la journée, et dernier passage seul |
| Contrôles de qualité | Résultat du dernier contrôle du jour (vrai / FAUX) |
| À surveiller | Contrôles en échec, sources qui perdent plus de 50 % de leurs articles (avec au moins 5 articles) et la règle responsable, règles qui n'ont rien rejeté |
| Top 5 des rejets | Les 5 couples (règle × source) qui rejettent le plus, avec la part des articles de la source et un exemple de titre |
| Rejets par règle | Toutes les règles, même à 0, avec les sources touchées |
| Rejets par source | Entrés, gardés, rejetés, taux, et règles en cause |
| Exemples | Les 3 titres rejetés les plus récents par règle, avec le détail (motif trouvé, doublon visé) |

Le top 5 est calculé par **couple règle × source**, et non par règle seule. Une règle qui écarte 1 % des articles au total peut écarter 60 % d'une seule source : c'est ce cas qu'il faut voir.

Les **exemples de titres** sont la partie la plus utile. Un compteur ne dit pas si une règle se trompe, un titre le dit. Le premier rapport, le 12/11/2026, a ainsi montré un faux positif de `R04_publi` (voir § 8).

## 8. Journal des ajustements

*À compléter après trois matins de lecture du rapport.*
